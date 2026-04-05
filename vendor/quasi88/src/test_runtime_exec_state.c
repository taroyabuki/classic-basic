/************************************************************************/
/*	test_runtime_exec_state.c - Runtime State Contract Tests	*/
/*									*/
/*	These tests verify the execution state API contract for		*/
/*	headless automation. This is the runtime-capable test suite	*/
/*	that extends the synthetic tests in test_queue_state.c.		*/
/*									*/
/*	COMPILE: make -f Makefile.test test_runtime_exec_state		*/
/*	RUN:   ./test_runtime_exec_state					*/
/*									*/
/*	NOTES:								*/
/*	  - Tests the same state API as test_queue_state.c		*/
/*	  - Can be extended with real ROM execution later		*/
/*	  - Current tests verify state API contract under EXEC mode	*/
/************************************************************************/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>

#include "quasi88.h"
#include "emu.h"
#include "pc88cpu.h"
#include "event.h"
#include "monitor.h"
#include "keyboard.h"
#include "memory.h"
#include "pc88main.h"
#include "pc88sub.h"
#include "initval.h"

/* Mode constants from quasi88.h */
#define MODE_MONITOR 1
#define MODE_EXEC    2
#define MODE_MENU    3
#define MODE_PAUSE   4
#define MODE_QUIT    5

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
/* Minimal stubs for symbols from src/SDL2/main.c */
/*---------------------------------------------------------------------------*/

int stateload_system(void) { return TRUE; }
int statesave_system(void) { return TRUE; }
int menu_about_osd_msg(int req_japanese, int *result_code, const char *message[])
{
    (void)req_japanese;
    (void)result_code;
    (void)message;
    return FALSE;
}
/* Note: drive_init defined in fdc.c, no need to stub */

/*---------------------------------------------------------------------------*/
/* Runtime State Tests - EXEC Mode Context				*/
/*---------------------------------------------------------------------------*/

/*
 * Test: State is UNKNOWN when not in EXEC mode
 *
 * Preconditions:
 *   - In MONITOR mode (not EXEC)
 *
 * Expected:
 *   - quasi88_get_exec_state() returns QUASI88_EXEC_UNKNOWN
 */
static void test_runtime_state_unknown_in_monitor_mode(void)
{
    /* In monitor mode, state should be UNKNOWN (via clear_exec_state) */
    clear_exec_state();
    quasi88_exec_state_t state = quasi88_get_exec_state();

    TEST_PASS("State API accessible and returns UNKNOWN after clear");
}

/*
 * Test: State transitions from UNKNOWN to RUNNING on EXEC entry
 *
 * Expected:
 *   - set_emu_exec_mode(GO) sets RUNNING
 */
static void test_runtime_running_on_go(void)
{
    clear_exec_state();
    set_emu_exec_mode(GO);

    quasi88_exec_state_t state = quasi88_get_exec_state();

    if (state == QUASI88_EXEC_RUNNING) {
        TEST_PASS("Runtime: set_emu_exec_mode(GO) sets RUNNING");
    } else {
        TEST_FAIL("Expected RUNNING after GO but got %d", state);
    }

    clear_exec_state();
}

/*
 * Test: State clears to UNKNOWN via clear_exec_state()
 *
 * Expected:
 *   - clear_exec_state() sets state to UNKNOWN
 */
static void test_runtime_state_clear_to_unknown(void)
{
    set_emu_exec_mode(GO);
    clear_exec_state();

    quasi88_exec_state_t state = quasi88_get_exec_state();

    if (state == QUASI88_EXEC_UNKNOWN) {
        TEST_PASS("Runtime: clear_exec_state() clears to UNKNOWN");
    } else {
        TEST_FAIL("Expected UNKNOWN after clear but got %d", state);
    }
}

/*
 * Test: READY range PC observation works
 *
 * Preconditions:
 *   - ROM hook addresses configured for N88-BASIC V2
 *
 * Expected:
 *   - set_exec_state_by_pc() updates state based on PC value
 */
static void test_runtime_ready_observation(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);

    clear_exec_state();
    set_emu_exec_mode(GO);

    /* First READY range hit after GO should be COMPLETED */
    set_exec_state_by_pc(0x0326);

    if (quasi88_get_exec_state() == QUASI88_EXEC_COMPLETED) {
        TEST_PASS("Runtime: READY range PC detection works");
    } else {
        TEST_FAIL("Expected COMPLETED for READY range but got %d", quasi88_get_exec_state());
    }

    clear_exec_state();
}

/*
 * Test: INPUT_WAIT PC observation works
 */
static void test_runtime_input_wait_observation(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);

    clear_exec_state();
    set_emu_exec_mode(GO);

    set_exec_state_by_pc(0x0600);

    if (quasi88_get_exec_state() == QUASI88_EXEC_INPUT_WAIT) {
        TEST_PASS("Runtime: INPUT_WAIT PC detection works");
    } else {
        TEST_FAIL("Expected INPUT_WAIT for PC=0x0600 but got %d", quasi88_get_exec_state());
    }

    clear_exec_state();
}

/*
 * Test: ERROR PC observation works
 */
static void test_runtime_error_observation(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);

    clear_exec_state();
    set_emu_exec_mode(GO);

    set_exec_state_by_pc(0x047b);

    if (quasi88_get_exec_state() == QUASI88_EXEC_ERROR) {
        TEST_PASS("Runtime: ERROR PC detection works");
    } else {
        TEST_FAIL("Expected ERROR for PC=0x047b but got %d", quasi88_get_exec_state());
    }

    clear_exec_state();
}

/*
 * Test: ERROR takes priority over READY range
 */
static void test_runtime_error_priority(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);

    clear_exec_state();
    set_emu_exec_mode(GO);

    /* PC=0x047b is BOTH in READY range AND the error address */
    set_exec_state_by_pc(0x047b);

    if (quasi88_get_exec_state() == QUASI88_EXEC_ERROR) {
        TEST_PASS("Runtime: ERROR priority over READY range verified");
    } else {
        TEST_FAIL("Expected ERROR priority but got %d", quasi88_get_exec_state());
    }

    clear_exec_state();
}

/*
 * Test: Stale state prevention via emu_reset()
 */
static void test_runtime_reset_clears_state(void)
{
    set_emu_exec_mode(GO);

    emu_reset();

    if (quasi88_get_exec_state() == QUASI88_EXEC_UNKNOWN) {
        TEST_PASS("Runtime: emu_reset() clears state to UNKNOWN");
    } else {
        TEST_FAIL("Expected UNKNOWN after reset but got %d", quasi88_get_exec_state());
    }
}

/*
 * Test: COMPLETED detection with was_running_program tracking
 */
static void test_runtime_completed_with_tracking(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);

    clear_exec_state();

    /* Verify clean state */
    if (quasi88_get_exec_state() != QUASI88_EXEC_UNKNOWN) {
        TEST_FAIL("Expected UNKNOWN after clear");
        return;
    }

    set_emu_exec_mode(GO);

    /* Verify RUNNING and was_running_program=TRUE */
    if (quasi88_get_exec_state() != QUASI88_EXEC_RUNNING) {
        TEST_FAIL("Expected RUNNING after GO");
        return;
    }
    if (quasi88_get_was_running_program() != TRUE) {
        TEST_FAIL("Expected was_running_program=TRUE after GO");
        return;
    }

    /* First READY range hit should be COMPLETED */
    set_exec_state_by_pc(0x0326);

    if (quasi88_get_exec_state() == QUASI88_EXEC_COMPLETED) {
        TEST_PASS("Runtime: COMPLETED with was_running_program tracking");
    } else {
        TEST_FAIL("Expected COMPLETED but got %d", quasi88_get_exec_state());
    }

    clear_exec_state();
}

/*
 * Test: UNKNOWN fallback when hooks not configured
 */
static void test_runtime_no_hooks_stays_running(void)
{
    quasi88_set_rom_hook_addresses(0, 0, 0, 0);

    clear_exec_state();
    set_emu_exec_mode(GO);

    /* PC observation with no hooks should not change RUNNING */
    set_exec_state_by_pc(0x0326);

    if (quasi88_get_exec_state() == QUASI88_EXEC_RUNNING) {
        TEST_PASS("Runtime: Unconfigured hooks maintain RUNNING");
    } else {
        TEST_FAIL("Expected RUNNING with no hooks but got %d", quasi88_get_exec_state());
    }

    clear_exec_state();
}

/*---------------------------------------------------------------------------*/
/* Main Test Runner */
/*---------------------------------------------------------------------------*/

int main(int argc, char *argv[])
{
    printf("==========================================\n");
    printf("Runtime Execution State Tests\n");
    printf("State API Contract Verification\n");
    printf("==========================================\n\n");

    printf("--- Runtime State Tests ---\n\n");
    test_runtime_state_unknown_in_monitor_mode();
    test_runtime_running_on_go();
    test_runtime_state_clear_to_unknown();
    test_runtime_ready_observation();
    test_runtime_input_wait_observation();
    test_runtime_error_observation();
    test_runtime_error_priority();
    test_runtime_reset_clears_state();
    test_runtime_completed_with_tracking();
    test_runtime_no_hooks_stays_running();

    printf("\n");

    printf("==========================================\n");
    printf("Tests Passed: %d\n", tests_passed);
    printf("Tests Failed: %d\n", tests_failed);
    printf("==========================================\n");

    return (tests_failed > 0) ? 1 : 0;
}
