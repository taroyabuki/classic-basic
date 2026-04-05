/*
 * test_completed_contract.c - Phase 7: COMPLETED and UNKNOWN contract verification
 *
 * This file tests the COMPLETED_POLICY and UNKNOWN_FALLBACK contracts.
 * Run via: vendor/quasi88/test_phase7_completed_contract.sh
 */

#include <stdio.h>
#include <assert.h>
#include <string.h>

#include "emu.h"

/* Test counter */
static int test_passed = 0;
static int test_failed = 0;

#define TEST_ASSERT(cond, msg) do { \
    if (cond) { \
        printf("[PASS] %s\n", msg); \
        test_passed++; \
    } else { \
        printf("[FAIL] %s\n", msg); \
        test_failed++; \
    } \
} while(0)

/*
 * Test 1: COMPLETED_POLICY=first_ready_transition
 * 
 * Verify that:
 * 1. When running a program and PC reaches READY range → COMPLETED
 * 2. Subsequent READY range hits → READY (not COMPLETED)
 */
static void test_completed_first_transition(void) {
    printf("\n--- Test: COMPLETED first transition ---\n");
    
    /* Set up ROM hooks for N88-BASIC V2 */
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    /* Simulate entering EXEC mode */
    set_emu_exec_mode(GO);
    
    /* Verify we start in RUNNING state */
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_RUNNING,
                "After GO mode, state should be RUNNING");
    
    /* Simulate was_running_program = TRUE (program was running) */
    quasi88_set_was_running_program(1);
    
    /* First hit of READY range → COMPLETED */
    set_exec_state_by_pc(0x0350);  /* Within READY range */
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_COMPLETED,
                "First READY range hit after RUNNING should be COMPLETED");
    
    /* Second hit of READY range → READY */
    set_exec_state_by_pc(0x0400);  /* Within READY range again */
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_READY,
                "Subsequent READY range hits should be READY");
}

/*
 * Test 2: UNKNOWN_FALLBACK=hooks_unconfigured
 * 
 * Verify that:
 * 1. When hooks are not configured (all 0), state remains UNKNOWN
 * 2. READY range check with addr=0 does not change state
 */
static void test_unknown_fallback_unconfigured(void) {
    printf("\n--- Test: UNKNOWN fallback unconfigured hooks ---\n");
    
    /* Set up hooks with all zeros (unconfigured) */
    quasi88_set_rom_hook_addresses(0, 0, 0, 0);
    
    /* Simulate entering EXEC mode */
    set_emu_exec_mode(GO);
    
    /* Try to set state via PC observation - should not change */
    set_exec_state_by_pc(0x0350);  /* Would be READY if configured */
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_RUNNING,
                "Unconfigured hooks should not change state from RUNNING");
    
    /* Even PC in READY range should not work */
    set_exec_state_by_pc(0x0400);
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_RUNNING,
                "PC in READY range with unconfigured hooks should not detect READY");
}

/*
 * Test 3: UNKNOWN after clear_exec_state
 * 
 * Verify that:
 * 1. clear_exec_state() sets state to UNKNOWN
 */
static void test_unknown_after_clear(void) {
    printf("\n--- Test: UNKNOWN after clear_exec_state ---\n");
    
    /* Set up ROM hooks */
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    /* Set some state */
    set_emu_exec_mode(GO);
    set_exec_state_by_pc(0x0350);
    
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_READY,
                "Should be READY before clear");
    
    /* Clear the state */
    clear_exec_state();
    
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_UNKNOWN,
                "clear_exec_state() should set state to UNKNOWN");
}

/*
 * Test 4: READY vs COMPLETED when not previously running
 * 
 * Verify that:
 * 1. If was_running_program is FALSE, READY range hit → READY (not COMPLETED)
 */
static void test_ready_when_not_running(void) {
    printf("\n--- Test: READY when not previously running ---\n");
    
    /* Set up ROM hooks */
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    
    /* Ensure was_running_program is FALSE */
    quasi88_set_was_running_program(0);
    
    /* Set state in READY range */
    set_exec_state_by_pc(0x0350);
    
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_READY,
                "READY range hit without prior RUNNING should be READY");
}

/*
 * Test 5: State priority ERROR > INPUT_WAIT > COMPLETED > READY
 * 
 * Verify the priority ordering is correct.
 */
static void test_state_priority(void) {
    printf("\n--- Test: State priority ---\n");
    
    /* Set up ROM hooks */
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);
    quasi88_set_was_running_program(1);
    
    /* ERROR at 0x047b (within READY range) should take priority */
    set_exec_state_by_pc(0x047b);
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_ERROR,
                "ERROR should take priority over READY range");
    
    /* INPUT_WAIT at 0x0600 */
    set_exec_state_by_pc(0x0600);
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_INPUT_WAIT,
                "INPUT_WAIT should be detected at its PC address");
    
    /* COMPLETED (first READY after RUNNING) */
    set_exec_state_by_pc(0x0350);
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_COMPLETED,
                "First READY after RUNNING should be COMPLETED");
    
    /* READY (subsequent) */
    set_exec_state_by_pc(0x0400);
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_READY,
                "Subsequent READY should be READY");
}

/*
 * Test 6: UNKNOWN when hooks are partially configured
 * 
 * Verify that:
 * 1. If only some hooks are non-zero, those can still work
 * 2. But we should be careful about partial configuration
 */
static void test_partial_hooks(void) {
    printf("\n--- Test: Partial hook configuration ---\n");
    
    /* Set up only READY range, no INPUT_WAIT or ERROR */
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0, 0);
    
    set_emu_exec_mode(GO);
    
    /* READY should still work */
    set_exec_state_by_pc(0x0350);
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_READY,
                "READY should work even with partial hooks");
    
    /* But ERROR should not be detected (pc=0x047b) */
    set_exec_state_by_pc(0x047b);
    /* Should still be READY since error_pc=0 */
    TEST_ASSERT(quasi88_get_exec_state() == QUASI88_EXEC_READY,
                "ERROR should not be detected when error_pc=0");
}

int main(int argc, char *argv[]) {
    printf("========================================\n");
    printf("Phase 7: COMPLETED/UNKNOWN Contract Tests\n");
    printf("========================================\n");
    printf("\nCOMPLETED_POLICY=first_ready_transition\n");
    printf("UNKNOWN_FALLBACK=hooks_unconfigured\n");
    printf("STATE_OWNER=set_emu_exec_mode\n");
    printf("STATE_OWNER=set_exec_state_by_pc\n");
    printf("STATE_OWNER=clear_exec_state\n");
    
    /* Run all tests */
    test_completed_first_transition();
    test_unknown_fallback_unconfigured();
    test_unknown_after_clear();
    test_ready_when_not_running();
    test_state_priority();
    test_partial_hooks();
    
    /* Summary */
    printf("\n========================================\n");
    printf("Phase 7 Test Summary\n");
    printf("========================================\n");
    printf("Passed: %d\n", test_passed);
    printf("Failed: %d\n", test_failed);
    printf("========================================\n");
    
    return (test_failed == 0) ? 0 : 1;
}
