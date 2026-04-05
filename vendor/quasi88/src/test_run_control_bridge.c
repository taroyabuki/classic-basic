/*
 * test_run_control_bridge.c - Control Bridge Unit Tests
 *
 * These tests verify the control bridge protocol implementation
 * directly, without relying on monitor text patterns or OCR.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "run_control_bridge.h"

static int pass_count = 0;
static int fail_count = 0;

#define ASSERT_EQ(expected, actual, msg) \
    if ((expected) != (actual)) { \
        printf("[FAIL] %s: expected %d, got %d\n", msg, expected, actual); \
        fail_count++; \
    } else { \
        printf("[PASS] %s\n", msg); \
        pass_count++; \
    }

#define ASSERT_STR_CONTAINS(str, substr, msg) \
    if (strstr(str, substr) == NULL) { \
        printf("[FAIL] %s: '%s' does not contain '%s'\n", msg, str, substr); \
        fail_count++; \
    } else { \
        printf("[PASS] %s\n", msg); \
        pass_count++; \
    }

#define ASSERT_TRUE(cond, msg) \
    if (!(cond)) { \
        printf("[FAIL] %s: condition not met\n", msg); \
        fail_count++; \
    } else { \
        printf("[PASS] %s\n", msg); \
        pass_count++; \
    }

/* Test: bridge_init returns OK on success */
static void test_bridge_init(void)
{
    printf("\n=== Test: bridge_init ===\n");
    int result = bridge_init();
    ASSERT_EQ(RUN_ERR_OK, result, "bridge_init should return OK");
}

/* Test: bridge_process_command handles LOAD with non-existent file */
static void test_load_file_not_found(void)
{
    printf("\n=== Test: LOAD file not found ===\n");
    bridge_response_t resp;
    int result = bridge_process_command("LOAD /nonexistent/path/file.bas ASCII", &resp);
    
    ASSERT_EQ(RUN_ERR_FILE_NOT_FOUND, result, "LOAD should return FILE_NOT_FOUND");
    ASSERT_TRUE(resp.error_code == RUN_ERR_FILE_NOT_FOUND, "response error_code should be FILE_NOT_FOUND");
    ASSERT_STR_CONTAINS(resp.response, "ERR RUN_ERR_FILE_NOT_FOUND", "response should contain ERR message");
}

/* Test: bridge_process_command handles STATE command */
static void test_state_command(void)
{
    printf("\n=== Test: STATE command ===\n");
    bridge_response_t resp;
    int result = bridge_process_command("STATE", &resp);
    
    ASSERT_EQ(RUN_ERR_OK, result, "STATE should return OK");
    ASSERT_TRUE(resp.error_code == RUN_ERR_OK, "response error_code should be OK");
    ASSERT_STR_CONTAINS(resp.response, "STATE", "response should contain STATE");
    ASSERT_STR_CONTAINS(resp.response, "MODE=", "response should contain MODE=");
    ASSERT_STR_CONTAINS(resp.response, "ROM=", "response should contain ROM=");
}

/* Test: bridge_process_command handles WAIT command */
static void test_wait_command(void)
{
    printf("\n=== Test: WAIT command ===\n");
    bridge_response_t resp;
    int result = bridge_process_command("WAIT 100", &resp);
    
    /* WAIT may timeout or succeed depending on state changes */
    ASSERT_TRUE(result == RUN_ERR_TIMEOUT || result == RUN_ERR_OK, 
                "WAIT should return TIMEOUT or OK");
    ASSERT_STR_CONTAINS(resp.response, 
                        (result == RUN_ERR_TIMEOUT) ? "ERR RUN_ERR_TIMEOUT" : "STATE",
                        "response should be TIMEOUT or STATE");
}

/* Test: bridge_process_command handles QUIT command */
static void test_quit_command(void)
{
    printf("\n=== Test: QUIT command ===\n");
    bridge_response_t resp;
    int result = bridge_process_command("QUIT", &resp);
    
    ASSERT_EQ(RUN_ERR_OK, result, "QUIT should return OK");
    ASSERT_TRUE(resp.error_code == RUN_ERR_OK, "response error_code should be OK");
    ASSERT_STR_CONTAINS(resp.response, "OK EXIT=", "response should contain OK EXIT=");
}

/* Test: bridge_process_command handles unknown command */
static void test_unknown_command(void)
{
    printf("\n=== Test: unknown command ===\n");
    bridge_response_t resp;
    int result = bridge_process_command("UNKNOWN_CMD", &resp);
    
    ASSERT_EQ(RUN_ERR_PROTOCOL, result, "unknown command should return PROTOCOL error");
    ASSERT_STR_CONTAINS(resp.response, "ERR RUN_ERR_PROTOCOL", "response should contain PROTOCOL error");
}

/* Test: bridge_process_command handles LOAD without path */
static void test_load_missing_path(void)
{
    printf("\n=== Test: LOAD without path ===\n");
    bridge_response_t resp;
    int result = bridge_process_command("LOAD", &resp);
    
    ASSERT_EQ(RUN_ERR_PROTOCOL, result, "LOAD without path should return PROTOCOL error");
    ASSERT_STR_CONTAINS(resp.response, "ERR RUN_ERR_PROTOCOL", "response should contain PROTOCOL error");
}

/* Test: bridge_get_state_name returns correct strings */
static void test_state_name_conversion(void)
{
    printf("\n=== Test: state name conversion ===\n");
    ASSERT_STR_CONTAINS(bridge_get_state_name(QUASI88_EXEC_UNKNOWN), "UNKNOWN", 
                        "UNKNOWN state should return 'UNKNOWN'");
    ASSERT_STR_CONTAINS(bridge_get_state_name(QUASI88_EXEC_RUNNING), "RUNNING",
                        "RUNNING state should return 'RUNNING'");
    ASSERT_STR_CONTAINS(bridge_get_state_name(QUASI88_EXEC_READY), "READY",
                        "READY state should return 'READY'");
    ASSERT_STR_CONTAINS(bridge_get_state_name(QUASI88_EXEC_INPUT_WAIT), "INPUT_WAIT",
                        "INPUT_WAIT state should return 'INPUT_WAIT'");
    ASSERT_STR_CONTAINS(bridge_get_state_name(QUASI88_EXEC_COMPLETED), "COMPLETED",
                        "COMPLETED state should return 'COMPLETED'");
    ASSERT_STR_CONTAINS(bridge_get_state_name(QUASI88_EXEC_ERROR), "ERROR",
                        "ERROR state should return 'ERROR'");
}

/* Test: bridge_get_mode_name returns correct strings */
static void test_mode_name_conversion(void)
{
    printf("\n=== Test: mode name conversion ===\n");
    ASSERT_STR_CONTAINS(bridge_get_mode_name(MONITOR), "MONITOR",
                        "MONITOR mode should return 'MONITOR'");
    ASSERT_STR_CONTAINS(bridge_get_mode_name(EXEC), "EXEC",
                        "EXEC mode should return 'EXEC'");
    ASSERT_STR_CONTAINS(bridge_get_mode_name(GO), "GO",
                        "GO mode should return 'GO'");
}

/* Cleanup after tests */
static void cleanup_tests(void)
{
    bridge_cleanup();
}

int main(void)
{
    printf("========================================\n");
    printf("Control Bridge Unit Tests\n");
    printf("========================================\n");

    test_bridge_init();
    test_load_file_not_found();
    test_state_command();
    test_wait_command();
    test_quit_command();
    test_unknown_command();
    test_load_missing_path();
    test_state_name_conversion();
    test_mode_name_conversion();

    cleanup_tests();

    printf("\n========================================\n");
    printf("Results: %d passed, %d failed\n", pass_count, fail_count);
    printf("========================================\n");

    return (fail_count > 0) ? 1 : 0;
}
