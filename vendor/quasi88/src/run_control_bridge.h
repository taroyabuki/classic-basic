/*
 * run_control_bridge.h - QUASI88 Dedicated Control Bridge
 *
 * This module provides a host-facing control channel separate from
 * monitor stdin. Commands are read from a dedicated fd/pipe and
 * dispatched to the QUASI88 emulator's state and input APIs.
 *
 * Protocol commands: LOAD, QUEUE, GO, STATE, WAIT, QUIT
 *
 * Design goals:
 *   - Deterministic control without monitor text parsing
 *   - Separate control channel from monitor stdin
 *   - Direct use of quasi88_keyinject, quasi88_get_exec_state, quasi88_get_mode
 */

#ifndef RUN_CONTROL_BRIDGE_H
#define RUN_CONTROL_BRIDGE_H

#include <stdint.h>

/* Error codes (match run-control.md protocol spec) */
#define RUN_ERR_OK              (0)
#define RUN_ERR_TIMEOUT         (1)
#define RUN_ERR_UNKNOWN         (2)
#define RUN_ERR_UNSUPPORTED_ROM (3)
#define RUN_ERR_HOOK_UNCONFIGURED (4)
#define RUN_ERR_FILE_NOT_FOUND  (5)
#define RUN_ERR_QUEUE_FULL      (6)
#define RUN_ERR_PROTOCOL        (7)
#define RUN_ERR_NOT_IN_MONITOR  (8)

/* Protocol response format */
typedef struct {
    int error_code;
    char response[8192];
} bridge_response_t;

/*
 * bridge_init - Initialize the control bridge
 *
 * Creates a dedicated control channel (pipe/fd) separate from monitor stdin.
 * This function sets up the data structures needed for command dispatch.
 *
 * Returns:
 *   0  Success
 *  -1  Initialization failed (pipe/create fd error)
 */
int bridge_init(void);

/*
 * bridge_process_command - Process a single control command
 *
 * Parses and executes one command from the input buffer.
 * Supported commands: LOAD, QUEUE, GO, STATE, WAIT, QUIT
 *
 * @input:  Null-terminated command string (e.g., "LOAD /path/to/file.bas ASCII")
 * @resp:   Buffer to receive response
 *
 * Command formats:
 *   LOAD <path> [ASCII]           - Load BASIC file
 *   QUEUE <string>                - Queue keyboard input
 *   GO                            - Start BASIC execution
 *   STATE                         - Query current state
 *   WAIT <timeout_ms>             - Poll until state change
 *   QUIT                          - Stop emulator
 *
 * Returns:
 *   RUN_ERR_OK on success
 *   Error code on failure
 */
int bridge_process_command(const char *input, bridge_response_t *resp);

/*
 * bridge_get_state_name - Convert exec state enum to string
 *
 * @state: quasi88_exec_state_t value
 *
 * Returns:
 *   String name: "UNKNOWN", "RUNNING", "READY", "INPUT_WAIT", "COMPLETED", "ERROR"
 */
const char *bridge_get_state_name(int state);

/*
 * bridge_get_mode_name - Convert mode enum to string
 *
 * @mode: quasi88 mode value
 *
 * Returns:
 *   String name: "EXEC", "GO", "TRACE", "STEP", "TRACE_CHANGE", "MONITOR", "MENU", "PAUSE", "QUIT"
 */
const char *bridge_get_mode_name(int mode);

/*
 * bridge_cleanup - Clean up bridge resources
 *
 * Closes control channel and releases allocated resources.
 * Called on emulator shutdown.
 */
void bridge_cleanup(void);

/*
 * bridge_accept_client - Accept a client connection
 *
 * Call this from the emulator's main loop to accept a new client.
 * Returns 0 on success, -1 on error or EAGAIN if no connection yet.
 */
int bridge_accept_client(void);

/*
 * bridge_receive_command - Receive a command from client
 *
 * Reads a command line from the client socket into buf.
 * Returns number of bytes read, or -1 on error/disconnect.
 *
 * @buf:    Buffer to receive command
 * @bufsize: Size of buffer
 */
int bridge_receive_command(char *buf, size_t bufsize);

/*
 * bridge_send_response - Send response to client
 *
 * Sends a response to the client with line-oriented framing.
 * Returns 0 on success, -1 on error.
 *
 * @resp:   bridge_response_t structure
 */
int bridge_send_response(const bridge_response_t *resp);

/*
 * bridge_has_client - Return whether a bridge client is currently connected.
 */
int bridge_has_client(void);
int bridge_is_bridge_only_mode(void);
int bridge_drain_injected_key_event(int *code_out, int *press_flag_out);

#endif /* RUN_CONTROL_BRIDGE_H */
