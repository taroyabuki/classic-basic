/************************************************************************/
/*	test_queue_state.c - Queue and State Contract Tests		*/
/*									*/
/*	These tests verify the queue and execution state API contracts	*/
/*	for headless automation of N88-BASIC.				*/
/*									*/
/*	COMPILE: make test_queue_state				*/
/*	RUN:   ./test_queue_state					*/
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
#include "drive.h"

/*
 * Minimal stubs for symbols that are normally provided by src/SDL2/main.c.
 * The dedicated test binary has its own main(), so we avoid linking that object.
 */
int stateload_system(void) { return TRUE; }
int statesave_system(void) { return TRUE; }
int menu_about_osd_msg(int req_japanese, int *result_code, const char *message[])
{
    (void)req_japanese;
    (void)result_code;
    (void)message;
    return FALSE;
}

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
/* Helper: minimal initialization for testing - only test state API */
/*---------------------------------------------------------------------------*/

static int init_for_test(void)
{
    /* For state API tests, we only need minimal init that doesn't require full screen */
    /* The state API is self-contained in emu.c and doesn't require full emulator init */
    return TRUE;
}

static void term_for_test(void)
{
    /* No cleanup needed for state API tests */
}

/*---------------------------------------------------------------------------*/
/* State Contract Tests - Direct Assertion Tests */
/*---------------------------------------------------------------------------*/

/*---------------------------------------------------------------------------*/
/* Queue Contract Tests - Direct Assertion Tests */
/*---------------------------------------------------------------------------*/

static void reset_queue_for_test(void)
{
    quasi88_keyinject_reset();
}

static int expect_drain_event(int expected_code, int expected_press_flag)
{
    int code;
    int press_flag;

    if (!quasi88_keyinject_drain(&code, &press_flag)) {
        TEST_FAIL("Expected drain event code=%d press=%d but queue was empty",
                  expected_code, expected_press_flag);
        return FALSE;
    }

    if (code != expected_code || press_flag != expected_press_flag) {
        TEST_FAIL("Expected drain event code=%d press=%d but got code=%d press=%d",
                  expected_code, expected_press_flag, code, press_flag);
        return FALSE;
    }

    return TRUE;
}

/*
 * Test: Empty queue does not drain
 */
static void test_queue_empty_drain(void)
{
    int code = -1;
    int press_flag = -1;

    reset_queue_for_test();

    if (quasi88_keyinject_drain(&code, &press_flag) == FALSE) {
        TEST_PASS("Empty queue returns FALSE on drain");
    } else {
        TEST_FAIL("Expected empty queue drain to return FALSE");
    }
}

/*
 * Test: FIFO order and one-event drain semantics
 *
 * Expected:
 *   - "AB" becomes A press, A release, B press, B release
 *   - one drain call consumes exactly one event
 */
static void test_queue_fifo_and_single_event_drain(void)
{
    reset_queue_for_test();

    if (quasi88_keyinject("AB") != 0) {
        TEST_FAIL("quasi88_keyinject(\"AB\") should succeed");
        return;
    }

    if (!expect_drain_event(KEY88_A, TRUE)) return;
    if (!expect_drain_event(KEY88_A, FALSE)) return;
    if (!expect_drain_event(KEY88_B, TRUE)) return;
    if (!expect_drain_event(KEY88_B, FALSE)) return;

    if (quasi88_keyinject_drain(&(int){0}, &(int){0}) == FALSE) {
        TEST_PASS("FIFO order and single-event drain verified");
    } else {
        TEST_FAIL("Queue should be empty after draining 4 events");
    }
}

/*
 * Test: Escape sequence maps to RETURN key
 */
static void test_queue_escape_sequence_return(void)
{
    reset_queue_for_test();

    if (quasi88_keyinject("<CR>") != 0) {
        TEST_FAIL("quasi88_keyinject(\"<CR>\") should succeed");
        return;
    }

    if (!expect_drain_event(KEY88_RETURN, TRUE)) return;
    if (!expect_drain_event(KEY88_RETURN, FALSE)) return;

    if (quasi88_keyinject_drain(&(int){0}, &(int){0}) == FALSE) {
        TEST_PASS("Escape sequence <CR> maps to RETURN press/release");
    } else {
        TEST_FAIL("Queue should be empty after draining RETURN press/release");
    }
}

/*
 * Test: Invalid sequence is rejected atomically
 *
 * Expected:
 *   - malformed escape returns -2
 *   - previously queued events stay intact
 */
static void test_queue_invalid_sequence_is_atomic(void)
{
    reset_queue_for_test();

    if (quasi88_keyinject("A") != 0) {
        TEST_FAIL("quasi88_keyinject(\"A\") should succeed");
        return;
    }

    if (quasi88_keyinject("<BAD>") == -2) {
        if (!expect_drain_event(KEY88_A, TRUE)) return;
        if (!expect_drain_event(KEY88_A, FALSE)) return;
        if (quasi88_keyinject_drain(&(int){0}, &(int){0}) == FALSE) {
            TEST_PASS("Invalid sequence is rejected without partial enqueue");
        } else {
            TEST_FAIL("Queue should contain only the original sequence after invalid enqueue");
        }
    } else {
        TEST_FAIL("Malformed escape should return -2");
    }
}

/*
 * Test: Overflow is rejected atomically
 *
 * Expected:
 *   - 32 ASCII chars fill the 64-entry queue
 *   - next enqueue returns -3
 *   - existing queue contents are unchanged
 */
static void test_queue_overflow_is_atomic(void)
{
    int i;
    const char *fill = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";

    reset_queue_for_test();

    if (quasi88_keyinject(fill) != 0) {
        TEST_FAIL("Filling queue with 32 ASCII chars should succeed");
        return;
    }

    if (quasi88_keyinject("B") != -3) {
        TEST_FAIL("Overflow enqueue should return -3");
        return;
    }

    for (i = 0; i < 32; i++) {
        if (!expect_drain_event(KEY88_A, TRUE)) return;
        if (!expect_drain_event(KEY88_A, FALSE)) return;
    }

    if (quasi88_keyinject_drain(&(int){0}, &(int){0}) == FALSE) {
        TEST_PASS("Overflow is atomic and preserves existing queue contents");
    } else {
        TEST_FAIL("Queue should be empty after draining filled contents");
    }
}

/*
 * Test: Reset flushes pending queue contents
 */
static void test_queue_reset_flushes_pending_input(void)
{
    reset_queue_for_test();

    if (quasi88_keyinject("A") != 0) {
        TEST_FAIL("quasi88_keyinject(\"A\") should succeed");
        return;
    }

    emu_reset();

    if (quasi88_keyinject_drain(&(int){0}, &(int){0}) == FALSE) {
        TEST_PASS("emu_reset() flushes pending queued input");
    } else {
        TEST_FAIL("Queue should be empty after emu_reset()");
    }
}

/*
 * Test: Empty sequence is accepted as a no-op
 */
static void test_queue_empty_sequence_is_noop(void)
{
    reset_queue_for_test();

    if (quasi88_keyinject("") != 0) {
        TEST_FAIL("Empty sequence should succeed as no-op");
        return;
    }

    if (quasi88_keyinject_drain(&(int){0}, &(int){0}) == FALSE) {
        TEST_PASS("Empty sequence is a no-op");
    } else {
        TEST_FAIL("Queue should remain empty after empty sequence");
    }
}

/*
 * Test: Initial state is UNKNOWN
 *
 * Preconditions:
 *   - System just started (after init)
 *
 * Expected:
 *   - quasi88_get_exec_state() returns QUASI88_EXEC_UNKNOWN
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
 * Test: clear_exec_state sets state to UNKNOWN
 *
 * Expected:
 *   - After calling clear_exec_state(), state is UNKNOWN
 */
static void test_state_clear_to_unknown(void)
{
    /* First, simulate some state by calling set_emu_exec_mode(GO) */
    set_emu_exec_mode(GO);
    
    /* Now clear it */
    clear_exec_state();
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_UNKNOWN) {
        TEST_PASS("clear_exec_state() sets state to UNKNOWN");
    } else {
        TEST_FAIL("Expected UNKNOWN after clear but got %d", state);
    }
}

/*
 * Test: set_emu_exec_mode(GO) sets RUNNING
 *
 * Expected:
 *   - After set_emu_exec_mode(GO), state is RUNNING
 */
static void test_state_running_on_go(void)
{
    clear_exec_state();  /* Start from UNKNOWN */
    set_emu_exec_mode(GO);
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_RUNNING) {
        TEST_PASS("set_emu_exec_mode(GO) sets RUNNING");
    } else {
        TEST_FAIL("Expected RUNNING after GO but got %d", state);
    }
}

/*
 * Test: set_exec_state_by_pc detects READY
 *
 * Preconditions:
 *   - ROM hook addresses configured for READY range
 *
 * Expected:
 *   - First READY range PC hit after GO emits COMPLETED
 *   - Subsequent READY range PC hits emit READY
 */
static void test_state_ready_detection(void)
{
    /* Configure hook addresses for N88-BASIC V2 ROM */
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);

    clear_exec_state();
    set_emu_exec_mode(GO);

    /* First READY range hit after GO emits COMPLETED */
    set_exec_state_by_pc(0x0326);

    quasi88_exec_state_t state = quasi88_get_exec_state();

    if (state == QUASI88_EXEC_COMPLETED) {
        TEST_PASS("First READY range PC emits COMPLETED (transition from RUNNING)");
    } else {
        TEST_FAIL("Expected COMPLETED for first PC=0x0326 but got %d", state);
    }

    /* Subsequent READY range PC should emit READY */
    set_exec_state_by_pc(0x0326);
    state = quasi88_get_exec_state();

    if (state == QUASI88_EXEC_READY) {
        TEST_PASS("Subsequent READY range PC detects READY");
    } else {
        TEST_FAIL("Expected READY for subsequent PC=0x0326 but got %d", state);
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
 *
 * Preconditions:
 *   - ROM hook addresses configured
 *
 * Expected:
 *   - Calling set_exec_state_by_pc at INPUT_WAIT address sets INPUT_WAIT state
 */
static void test_state_input_wait_detection(void)
{
    /* Configure hook addresses */
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    clear_exec_state();
    set_emu_exec_mode(GO);
    
    /* Simulate PC reaching INPUT_WAIT address */
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
 *
 * Preconditions:
 *   - ROM hook addresses configured
 *
 * Expected:
 *   - Calling set_exec_state_by_pc at ERROR address sets ERROR state
 */
static void test_state_error_detection(void)
{
    /* Configure hook addresses */
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    clear_exec_state();
    set_emu_exec_mode(GO);
    
    /* Simulate PC reaching ERROR address */
    set_exec_state_by_pc(0x047b);
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_ERROR) {
        TEST_PASS("set_exec_state_by_pc(0x047b) detects ERROR");
    } else {
        TEST_FAIL("Expected ERROR for PC=0x047b but got %d", state);
    }
}

/*
 * Test: State priority - ERROR takes precedence over READY
 *
 * Expected:
 *   - ERROR address check happens before READY range check
 */
static void test_state_error_priority(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    clear_exec_state();
    set_emu_exec_mode(GO);
    
    /* ERROR at 0x047b is within READY range 0x0320-0x04e0 */
    /* But ERROR should take priority */
    set_exec_state_by_pc(0x047b);
    
    quasi88_exec_state_t state = quasi88_get_exec_state();
    
    if (state == QUASI88_EXEC_ERROR) {
        TEST_PASS("ERROR state has priority over READY range");
    } else {
        TEST_FAIL("Expected ERROR (priority) for PC=0x047b but got %d", state);
    }
}

/*
 * Test: ROM hook addresses can be configured
 *
 * Expected:
 *   - quasi88_set_rom_hook_addresses accepts and stores addresses
 */
static void test_rom_hook_configuration(void)
{
    /* This test verifies the API exists and accepts parameters */
    /* The actual effect is tested in other state detection tests */
    quasi88_set_rom_hook_addresses(0x100, 0x200, 0x300, 0x400);

    /* If we got here without crash, configuration succeeded */
    TEST_PASS("quasi88_set_rom_hook_addresses accepts configuration");
}

/*
 * Test: COMPLETED detection on transition from RUNNING to READY range
 *
 * Preconditions:
 *   - set_emu_exec_mode(GO) was called (sets RUNNING and was_running_program)
 *   - ROM hook addresses configured for READY range
 *
 * Expected:
 *   - First READY range PC hit after GO emits COMPLETED
 *   - Subsequent READY range PC hits emit READY
 */
static void test_state_completed_detection(void)
{
    /* Configure hook addresses */
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);

    /* Ensure clean state: CLEAR first, then GO */
    clear_exec_state();
    
    /* Verify state after clear */
    quasi88_exec_state_t state_before = quasi88_get_exec_state();
    if (state_before != QUASI88_EXEC_UNKNOWN) {
        TEST_FAIL("After clear_exec_state, expected UNKNOWN but got %d", state_before);
        return;
    }
    
    /* Verify was_running_program is FALSE after clear */
    if (quasi88_get_was_running_program() != FALSE) {
        TEST_FAIL("After clear_exec_state, expected was_running_program=FALSE but got %d",
                  quasi88_get_was_running_program());
        return;
    }
    
    set_emu_exec_mode(GO);  /* Sets RUNNING, was_running_program=TRUE */
    
    /* Verify state after GO */
    quasi88_exec_state_t state_after_go = quasi88_get_exec_state();
    if (state_after_go != QUASI88_EXEC_RUNNING) {
        TEST_FAIL("After set_emu_exec_mode(GO), expected RUNNING but got %d", state_after_go);
        return;
    }
    
    /* Verify was_running_program is TRUE after GO */
    if (quasi88_get_was_running_program() != TRUE) {
        TEST_FAIL("After set_emu_exec_mode(GO), expected was_running_program=TRUE but got %d",
                  quasi88_get_was_running_program());
        return;
    }

    /* First READY range hit after GO should detect COMPLETED */
    set_exec_state_by_pc(0x0326);

    quasi88_exec_state_t state = quasi88_get_exec_state();

    if (state == QUASI88_EXEC_COMPLETED) {
        TEST_PASS("set_exec_state_by_pc(0x0326) detects COMPLETED after GO");
    } else {
        /* Debug: print what we got */
        TEST_FAIL("Expected COMPLETED (4) for PC=0x0326 after GO but got %d", state);
    }

    /* Subsequent READY range hit should detect READY (not COMPLETED again) */
    set_exec_state_by_pc(0x0400);
    state = quasi88_get_exec_state();
    if (state == QUASI88_EXEC_READY) {
        TEST_PASS("Subsequent READY range PC detects READY (not COMPLETED)");
    } else {
        TEST_FAIL("Expected READY for subsequent PC=0x0400 but got %d", state);
    }
}

/*
 * Test: COMPLETED does not emit if not in RUNNING (was_running_program=FALSE)
 *
 * Expected:
 *   - Without set_emu_exec_mode(GO), READY range PC emits READY directly
 */
static void test_state_no_completed_without_running(void)
{
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);

    /* Don't call set_emu_exec_mode(GO) - was_running_program stays FALSE */
    clear_exec_state();  /* State is UNKNOWN */

    /* READY range PC should emit READY directly (not COMPLETED) */
    set_exec_state_by_pc(0x0326);

    quasi88_exec_state_t state = quasi88_get_exec_state();

    if (state == QUASI88_EXEC_READY) {
        TEST_PASS("READY range PC without RUNNING emits READY directly");
    } else {
        TEST_FAIL("Expected READY without RUNNING but got %d", state);
    }
}

/*---------------------------------------------------------------------------*/
/* Main Test Runner */
/*---------------------------------------------------------------------------*/

int main(int argc, char *argv[])
{
    printf("==========================================\n");
    printf("Queue and State Contract Tests\n");
    printf("Direct Assertion Tests for N88-BASIC\n");
    printf("==========================================\n\n");

    /* Initialize for state tests */
    printf("--- Initialization ---\n");
    if (!init_for_test()) {
        fprintf(stderr, "Initialization failed\n");
        return 1;
    }
    printf("[INFO] Test initialization complete\n\n");

    printf("--- Queue Contract Tests ---\n\n");
    test_queue_empty_drain();
    test_queue_fifo_and_single_event_drain();
    test_queue_escape_sequence_return();
    test_queue_invalid_sequence_is_atomic();
    test_queue_overflow_is_atomic();
    test_queue_reset_flushes_pending_input();
    test_queue_empty_sequence_is_noop();

    printf("\n--- State Contract Tests ---\n\n");
    test_state_initial_unknown();
    test_state_clear_to_unknown();
    test_state_running_on_go();
    test_state_ready_detection();
    test_state_input_wait_detection();
    test_state_error_detection();
    test_state_error_priority();
    test_state_completed_detection();
    test_state_no_completed_without_running();
    test_rom_hook_configuration();
    
    printf("\n");

    printf("==========================================\n");
    printf("Tests Passed: %d\n", tests_passed);
    printf("Tests Failed: %d\n", tests_failed);
    printf("==========================================\n");

    /* Cleanup */
    term_for_test();

    return (tests_failed > 0) ? 1 : 0;
}
