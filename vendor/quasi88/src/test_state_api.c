/************************************************************************/
/*	test_state_api.c - Minimal State API Tests			*/
/*									*/
/*	Tests quasi88_get_exec_state(), set_emu_exec_mode(),		*/
/*	clear_exec_state(), set_exec_state_by_pc() directly.		*/
/*									*/
/*	This is a minimal test that only tests the emu.c state API.	*/
/*	No full emulator initialization required.			*/
/************************************************************************/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>

/* Forward declarations - tested functions from emu.c */
typedef enum {
    QUASI88_EXEC_UNKNOWN = 0,
    QUASI88_EXEC_RUNNING,
    QUASI88_EXEC_READY,
    QUASI88_EXEC_INPUT_WAIT,
    QUASI88_EXEC_COMPLETED,
    QUASI88_EXEC_ERROR
} quasi88_exec_state_t;

typedef unsigned short word;

extern quasi88_exec_state_t quasi88_get_exec_state(void);
extern void clear_exec_state(void);
extern void set_exec_state_by_pc(word pc);
extern void quasi88_set_rom_hook_addresses(word ready_low, word ready_high,
                                           word input_wait_pc, word error_pc);

/* External function from emu.c - we can't directly test set_emu_exec_mode */
/* because it depends on emu_mode_execute global which requires more context */
extern void set_emu_exec_mode(int mode);

#define GO 1

/* Test result counters */
static int tests_passed = 0;
static int tests_failed = 0;

#define TEST_PASS(fmt, ...) do { \
    printf("[PASS] " fmt "\n", ##__VA_ARGS__); \
    tests_passed++; \
} while(0)

#define TEST_FAIL(fmt, ...) do { \
    printf("[FAIL] " fmt "\n", ##__VA_ARGS__); \
    tests_failed++; \
} while(0)

/*---------------------------------------------------------------------------*/
/* State Contract Tests - Direct Assertion Tests */
/*---------------------------------------------------------------------------*/

/*
 * Test: Initial state is UNKNOWN
 */
static void test_state_initial_unknown(void)
{
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_UNKNOWN) {
        TEST_PASS("Initial state is UNKNOWN");
    } else {
        TEST_FAIL("Expected UNKNOWN but got %d", state);
    }
}

/*
 * Test: set_emu_exec_mode(GO) sets RUNNING
 */
static void test_state_running_on_go(void)
{
    clear_exec_state();  /* Ensure start from UNKNOWN */
    set_emu_exec_mode(GO);
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_RUNNING) {
        TEST_PASS("set_emu_exec_mode(GO) sets RUNNING");
    } else {
        TEST_FAIL("Expected RUNNING after GO but got %d", state);
    }
}

/*
 * Test: clear_exec_state sets state to UNKNOWN
 */
static void test_state_clear_to_unknown(void)
{
    set_emu_exec_mode(GO);
    clear_exec_state();
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_UNKNOWN) {
        TEST_PASS("clear_exec_state() sets state to UNKNOWN");
    } else {
        TEST_FAIL("Expected UNKNOWN after clear but got %d", state);
    }
}

/*
 * Test: set_exec_state_by_pc detects READY
 */
static void test_state_ready_detection(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    clear_exec_state();
    set_emu_exec_mode(GO);
    
    /* Simulate PC reaching READY range */
    set_exec_state_by_pc(0x0326);
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_READY) {
        TEST_PASS("set_exec_state_by_pc(0x0326) detects READY");
    } else {
        TEST_FAIL("Expected READY for PC=0x0326 but got %d", state);
    }
    
    /* Test upper bound of READY range */
    set_exec_state_by_pc(0x04e0);
    state = quasi88_get_exec_state();
    if (state == QUASI88_EXEC_READY) {
        TEST_PASS("set_exec_state_by_pc(0x04e0) detects READY");
    } else {
        TEST_FAIL("Expected READY for PC=0x04e0 but got %d", state);
    }
}

/*
 * Test: set_exec_state_by_pc detects INPUT_WAIT
 */
static void test_state_input_wait_detection(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    clear_exec_state();
    set_emu_exec_mode(GO);
    
    set_exec_state_by_pc(0x0600);
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_INPUT_WAIT) {
        TEST_PASS("set_exec_state_by_pc(0x0600) detects INPUT_WAIT");
    } else {
        TEST_FAIL("Expected INPUT_WAIT for PC=0x0600 but got %d", state);
    }
}

/*
 * Test: set_exec_state_by_pc detects ERROR
 */
static void test_state_error_detection(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    clear_exec_state();
    set_emu_exec_mode(GO);
    
    set_exec_state_by_pc(0x047b);
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_ERROR) {
        TEST_PASS("set_exec_state_by_pc(0x047b) detects ERROR");
    } else {
        TEST_FAIL("Expected ERROR for PC=0x047b but got %d", state);
    }
}

/*
 * Test: ERROR takes precedence over READY
 */
static void test_state_error_priority(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    clear_exec_state();
    set_emu_exec_mode(GO);
    
    /* ERROR at 0x047b is within READY range 0x0320-0x04e0 */
    set_exec_state_by_pc(0x047b);
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_ERROR) {
        TEST_PASS("ERROR state has priority over READY range");
    } else {
        TEST_FAIL("Expected ERROR (priority) for PC=0x047b but got %d", state);
    }
}

/*
 * Test: Out of range PC does not change state
 */
static void test_state_no_change_outside_range(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    clear_exec_state();
    set_emu_exec_mode(GO);
    
    /* PC outside all hook ranges should not change state */
    set_exec_state_by_pc(0x1000);
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    /* Should remain RUNNING since no hook matched */
    if (state == QUASI88_EXEC_RUNNING) {
        TEST_PASS("Out-of-range PC does not change state");
    } else {
        TEST_FAIL("Expected RUNNING for PC=0x1000 but got %d", state);
    }
}

/*
 * Test: ROM hook addresses can be configured
 */
static void test_rom_hook_configuration(void)
{
    /* Test with custom addresses */
    quasi88_set_rom_hook_addresses(0x100, 0x200, 0x300, 0x400);
    
    clear_exec_state();
    set_emu_exec_mode(GO);
    
    /* Custom READY range */
    set_exec_state_by_pc(0x150);
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_READY) {
        TEST_PASS("Custom ROM hook addresses work");
    } else {
        TEST_FAIL("Expected READY with custom hooks but got %d", state);
    }
}

/*---------------------------------------------------------------------------*/
/* Main Test Runner */
/*---------------------------------------------------------------------------*/

int main(int argc, char *argv[])
{
    printf("==========================================\n");
    printf("State API Direct Assertion Tests\n");
    printf("Testing quasi88_get_exec_state and related APIs\n");
    printf("==========================================\n\n");

    printf("--- State Contract Tests ---\n\n");
    
    test_state_initial_unknown();
    test_state_running_on_go();
    test_state_clear_to_unknown();
    test_state_ready_detection();
    test_state_input_wait_detection();
    test_state_error_detection();
    test_state_error_priority();
    test_state_no_change_outside_range();
    test_rom_hook_configuration();
    
    printf("\n");

    printf("==========================================\n");
    printf("Tests Passed: %d\n", tests_passed);
    printf("Tests Failed: %d\n", tests_failed);
    printf("==========================================\n");

    return (tests_failed > 0) ? 1 : 0;
}
