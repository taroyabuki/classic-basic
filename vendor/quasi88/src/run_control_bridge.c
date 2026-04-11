/*
 * run_control_bridge.c - QUASI88 Dedicated Control Bridge Implementation
 *
 * This module implements a host-facing control channel that is separate from
 * the monitor stdin path. Commands are dispatched to the QUASI88 emulator's
 * native APIs:
 *
 *   - quasi88_keyinject()     - Queue keyboard input for ASCII injection
 *   - quasi88_get_exec_state() - Query BASIC interpreter state
 *   - quasi88_get_mode()      - Query emulator mode
 *
 * The control channel uses a Unix domain socket, distinct from monitor stdin.
 * This ensures that BASIC execution input does not mix with monitor commands.
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sched.h>
#include <time.h>
#include <errno.h>
#include <ctype.h>
#include <sys/socket.h>
#include <sys/un.h>

#include "run_control_bridge.h"
#include "quasi88.h"
#include "emu.h"
#include "event.h"
#include "basic.h"
#include "keyboard.h"
#include "memory.h"
#include "screen.h"
#include "crtcdmac.h"

/* Control channel: Unix domain socket from monitor stdin */
static int control_socket_fd = -1;
static int client_socket_fd = -1;
static const char *default_socket_path = "/tmp/n88basic-control.sock";
static char configured_socket_path[sizeof(((struct sockaddr_un *)0)->sun_path)] = {0};
typedef struct {
    int key_code;
    int press_flag;
} bridge_key_event_t;

#define BRIDGE_KEY_EVENT_QUEUE_SIZE 2048
#define BRIDGE_KEY_PRESS_REPEAT_COUNT 6
#define BRIDGE_KEY_RELEASE_REPEAT_COUNT 2
static bridge_key_event_t bridge_key_events[BRIDGE_KEY_EVENT_QUEUE_SIZE];
static int bridge_key_head = 0;
static int bridge_key_tail = 0;
static int bridge_key_count = 0;

/* Track loaded ROM ID for STATE command */
static char loaded_rom_id[64] = "n88vm1";
static int bridge_initialized = 0;
static int bridge_only_mode = 0;
static const char *startup_sequence = NULL;
static size_t startup_sequence_pos = 0;
static int startup_sequence_press_phase = 1;
static int startup_sequence_hold_frames = 0;
static int startup_files_prompt_attempts = 0;

#define BRIDGE_AUTOMATION_PRESS_FRAMES 6
#define BRIDGE_AUTOMATION_RELEASE_FRAMES 2

static int bridge_ascii_to_key88(unsigned char c);
static int bridge_ascii_to_key88_physical(unsigned char c, int *key_code_out, int *needs_shift_out);
static size_t capture_text_screen(char *buf, size_t bufsize);

static void bridge_reset_startup_automation(void)
{
    startup_sequence = NULL;
    startup_sequence_pos = 0;
    startup_sequence_press_phase = 1;
    startup_sequence_hold_frames = 0;
    startup_files_prompt_attempts = 0;
}

static void bridge_schedule_startup_sequence(const char *sequence)
{
    if (!sequence || startup_sequence) {
        return;
    }
    startup_sequence = sequence;
    startup_sequence_pos = 0;
    startup_sequence_press_phase = 1;
    startup_sequence_hold_frames = 0;
}

static int bridge_sequence_char_to_key88(char ch)
{
    if (ch == '\r' || ch == '\n') {
        return KEY88_RETURN;
    }
    return bridge_ascii_to_key88((unsigned char)toupper((unsigned char)ch));
}

static void bridge_drive_startup_sequence(void)
{
    int key_code;
    char ch;

    if (!startup_sequence || startup_sequence[startup_sequence_pos] == '\0') {
        startup_sequence = NULL;
        return;
    }

    ch = startup_sequence[startup_sequence_pos];
    key_code = bridge_sequence_char_to_key88(ch);
    if (key_code == KEY88_INVALID) {
        startup_sequence = NULL;
        return;
    }

    if (startup_sequence_press_phase) {
        quasi88_key(key_code, TRUE);
        startup_sequence_hold_frames++;
        if (startup_sequence_hold_frames >= BRIDGE_AUTOMATION_PRESS_FRAMES) {
            startup_sequence_press_phase = 0;
            startup_sequence_hold_frames = 0;
        }
        return;
    }

    quasi88_key(key_code, FALSE);
    startup_sequence_hold_frames++;
    if (startup_sequence_hold_frames >= BRIDGE_AUTOMATION_RELEASE_FRAMES) {
        startup_sequence_press_phase = 1;
        startup_sequence_hold_frames = 0;
        startup_sequence_pos++;
        if (startup_sequence[startup_sequence_pos] == '\0') {
            startup_sequence = NULL;
        }
    }
}

static void bridge_drive_startup_prompt_automation(void)
{
    char screen[2048];
    size_t screen_len;

    if (!bridge_only_mode || !quasi88_is_exec()) {
        return;
    }

    screen_len = capture_text_screen(screen, sizeof(screen));
    if (screen_len == 0) {
        bridge_drive_startup_sequence();
        return;
    }

    if (strstr(screen, "How many files(0-15)?")) {
        if (!startup_sequence && startup_files_prompt_attempts == 0) {
            bridge_schedule_startup_sequence("2\r");
            startup_files_prompt_attempts++;
        }
    }

    bridge_drive_startup_sequence();
}

static void bridge_reset_injected_key_events(void)
{
    bridge_key_head = 0;
    bridge_key_tail = 0;
    bridge_key_count = 0;
}

static int bridge_ascii_to_key88(unsigned char c)
{
    if (c == 0x08 || c == 0x7f) {
        return KEY88_BS;
    }
    if (c >= 32 && c <= 126) {
        return (int)c;
    }
    return KEY88_INVALID;
}

static int bridge_ascii_to_key88_physical(unsigned char c, int *key_code_out, int *needs_shift_out)
{
    int key_code = KEY88_INVALID;
    int needs_shift = 0;

    if (c == 0x08 || c == 0x7f) {
        key_code = KEY88_BS;
    } else if (c >= 'a' && c <= 'z') {
        key_code = c;
    } else if (c >= 'A' && c <= 'Z') {
        key_code = c - 'A' + 'a';
        needs_shift = 1;
    } else if (c >= '0' && c <= '9') {
        key_code = c;
    } else {
        switch (c) {
        case ' ':
            key_code = KEY88_SPACE;
            break;
        case '!':
            key_code = KEY88_1;
            needs_shift = 1;
            break;
        case '"':
            key_code = KEY88_2;
            needs_shift = 1;
            break;
        case '#':
            key_code = KEY88_3;
            needs_shift = 1;
            break;
        case '$':
            key_code = KEY88_4;
            needs_shift = 1;
            break;
        case '%':
            key_code = KEY88_5;
            needs_shift = 1;
            break;
        case '&':
            key_code = KEY88_6;
            needs_shift = 1;
            break;
        case '\'':
            key_code = KEY88_7;
            needs_shift = 1;
            break;
        case '(':
            key_code = KEY88_8;
            needs_shift = 1;
            break;
        case ')':
            key_code = KEY88_9;
            needs_shift = 1;
            break;
        case '-':
            key_code = KEY88_MINUS;
            break;
        case '=':
            key_code = KEY88_MINUS;
            needs_shift = 1;
            break;
        case '^':
            key_code = KEY88_CARET;
            break;
        case '~':
            key_code = KEY88_CARET;
            needs_shift = 1;
            break;
        case '@':
            key_code = KEY88_AT;
            break;
        case '`':
            key_code = KEY88_AT;
            needs_shift = 1;
            break;
        case '[':
            key_code = KEY88_BRACKETLEFT;
            break;
        case '{':
            key_code = KEY88_BRACKETLEFT;
            needs_shift = 1;
            break;
        case '\\':
            key_code = KEY88_YEN;
            break;
        case '|':
            key_code = KEY88_YEN;
            needs_shift = 1;
            break;
        case ']':
            key_code = KEY88_BRACKETRIGHT;
            break;
        case '}':
            key_code = KEY88_BRACKETRIGHT;
            needs_shift = 1;
            break;
        case '_':
            key_code = KEY88_UNDERSCORE;
            break;
        case ':':
            key_code = KEY88_COLON;
            break;
        case '*':
            key_code = KEY88_COLON;
            needs_shift = 1;
            break;
        case ';':
            key_code = KEY88_SEMICOLON;
            break;
        case '+':
            key_code = KEY88_SEMICOLON;
            needs_shift = 1;
            break;
        case ',':
            key_code = KEY88_COMMA;
            break;
        case '<':
            key_code = KEY88_COMMA;
            needs_shift = 1;
            break;
        case '.':
            key_code = KEY88_PERIOD;
            break;
        case '>':
            key_code = KEY88_PERIOD;
            needs_shift = 1;
            break;
        case '/':
            key_code = KEY88_SLASH;
            break;
        case '?':
            key_code = KEY88_SLASH;
            needs_shift = 1;
            break;
        default:
            break;
        }
    }

    if (key_code_out) {
        *key_code_out = key_code;
    }
    if (needs_shift_out) {
        *needs_shift_out = needs_shift;
    }
    return key_code;
}

static int bridge_enqueue_key_event(int key_code, int press_flag)
{
    if (bridge_key_count >= BRIDGE_KEY_EVENT_QUEUE_SIZE) {
        return -1;
    }

    bridge_key_events[bridge_key_tail].key_code = key_code;
    bridge_key_events[bridge_key_tail].press_flag = press_flag;
    bridge_key_tail = (bridge_key_tail + 1) % BRIDGE_KEY_EVENT_QUEUE_SIZE;
    bridge_key_count++;
    return 0;
}

static int bridge_queue_key_press_release(int key_code)
{
    int i;

    for (i = 0; i < BRIDGE_KEY_PRESS_REPEAT_COUNT; i++) {
        if (bridge_enqueue_key_event(key_code, TRUE) < 0) {
            return -3;
        }
    }
    for (i = 0; i < BRIDGE_KEY_RELEASE_REPEAT_COUNT; i++) {
        if (bridge_enqueue_key_event(key_code, FALSE) < 0) {
            return -3;
        }
    }
    return 0;
}

static int bridge_queue_ascii_char(unsigned char c)
{
    int key_code = KEY88_INVALID;
    int needs_shift = 0;
    int i;

    if (bridge_ascii_to_key88_physical(c, &key_code, &needs_shift) == KEY88_INVALID) {
        return -2;
    }

    if (needs_shift) {
        for (i = 0; i < BRIDGE_KEY_PRESS_REPEAT_COUNT; i++) {
            if (bridge_enqueue_key_event(KEY88_SHIFT, TRUE) < 0) {
                return -3;
            }
        }
    }

    if (bridge_queue_key_press_release(key_code) < 0) {
        return -3;
    }

    if (needs_shift) {
        for (i = 0; i < BRIDGE_KEY_RELEASE_REPEAT_COUNT; i++) {
            if (bridge_enqueue_key_event(KEY88_SHIFT, FALSE) < 0) {
                return -3;
            }
        }
    }

    return 0;
}

static int bridge_enqueue_sequence_immediate(const char *sequence)
{
    const char *p = sequence;
    int needed = 0;

    if (!sequence) {
        return -2;
    }

    while (*p != '\0') {
        if (*p == '<') {
            const char *end = strchr(p, '>');
            int len;
            if (!end) {
                return -2;
            }
            len = (int)(end - p);
            if (!((len == 3 && strncmp(p + 1, "CR", 2) == 0) ||
                  (len == 3 && strncmp(p + 1, "BS", 2) == 0) ||
                  (len == 4 && strncmp(p + 1, "RET", 3) == 0) ||
                  (len == 4 && strncmp(p + 1, "ENT", 3) == 0))) {
                return -2;
            }
            needed += BRIDGE_KEY_PRESS_REPEAT_COUNT + BRIDGE_KEY_RELEASE_REPEAT_COUNT;
            p = end + 1;
            continue;
        }

        {
            int key_code = KEY88_INVALID;
            int needs_shift = 0;
            if (bridge_ascii_to_key88_physical((unsigned char)*p, &key_code, &needs_shift) == KEY88_INVALID) {
                return -2;
            }
            needed += BRIDGE_KEY_PRESS_REPEAT_COUNT + BRIDGE_KEY_RELEASE_REPEAT_COUNT;
            if (needs_shift) {
                needed += BRIDGE_KEY_PRESS_REPEAT_COUNT + BRIDGE_KEY_RELEASE_REPEAT_COUNT;
            }
        }
        p++;
    }

    if (bridge_key_count + needed > BRIDGE_KEY_EVENT_QUEUE_SIZE) {
        return -3;
    }

    p = sequence;
    while (*p != '\0') {
        int key_code;
        if (*p == '<') {
            const char *end = strchr(p, '>');
            int len = (int)(end - p);
            if ((len == 3 && strncmp(p + 1, "CR", 2) == 0) ||
                (len == 3 && strncmp(p + 1, "BS", 2) == 0) ||
                (len == 4 && strncmp(p + 1, "RET", 3) == 0) ||
                (len == 4 && strncmp(p + 1, "ENT", 3) == 0)) {
                if (len == 3 && strncmp(p + 1, "BS", 2) == 0) {
                    key_code = KEY88_BS;
                } else {
                    key_code = KEY88_RETURN;
                }
            } else {
                return -2;
            }
            p = end + 1;
        } else {
            if (bridge_queue_ascii_char((unsigned char)*p) < 0) {
                return -3;
            }
            p++;
            continue;
        }

        if (bridge_queue_key_press_release(key_code) < 0) {
            return -3;
        }
    }

    return 0;
}

static size_t capture_text_screen(char *buf, size_t bufsize)
{
    int lines = (grph_ctrl & 0x20) ? 25 : 20;
    int width = (sys_ctrl & 0x01) ? 80 : 40;
    size_t pos = 0;
    int i, j;

    if (!buf || bufsize == 0) {
        return 0;
    }

    for (i = 0; i < lines && pos + 1 < bufsize; i++) {
        int end = -1;
        char line[81];
        memset(line, ' ', sizeof(line));

        for (j = 0; j < width; j++) {
            Ushort cell = (width == 80)
                ? text_attr_buf[text_attr_flipflop][i * 80 + j]
                : text_attr_buf[text_attr_flipflop][i * 80 + j * 2];
            char ch = (char)(cell >> 8);
            if (ch == '\0') {
                ch = ' ';
            }
            line[j] = ch;
            if (ch != ' ') {
                end = j;
            }
        }

        /*
         * Preserve trailing spaces on the active input line. textscr used by the
         * host CLI should reflect cursor advance after typing "10 " even though
         * the last visible character cell is blank.
         */
        if (i == crtc_cursor[1]) {
            int cursor_tail = crtc_cursor[0] - 1;
            if (cursor_tail > end) {
                end = cursor_tail;
            }
        }

        if (end < 0) {
            continue;
        }
        if (pos + (size_t)end + 2 >= bufsize) {
            break;
        }
        memcpy(buf + pos, line, (size_t)end + 1);
        pos += (size_t)end + 1;
        buf[pos++] = '\n';
    }

    buf[pos] = '\0';
    return pos;
}

static int cmd_textscr(bridge_response_t *resp)
{
    char screen[2048];
    static const char hex[] = "0123456789ABCDEF";
    size_t screen_len = capture_text_screen(screen, sizeof(screen));
    size_t pos = 0;
    size_t i;

    pos += snprintf(resp->response + pos, sizeof(resp->response) - pos, "OK TEXTHEX=");
    for (i = 0; i < screen_len && pos + 2 < sizeof(resp->response); i++) {
        unsigned char byte = (unsigned char)screen[i];
        resp->response[pos++] = hex[byte >> 4];
        resp->response[pos++] = hex[byte & 0x0F];
    }
    resp->response[pos] = '\0';
    resp->error_code = RUN_ERR_OK;
    return RUN_ERR_OK;
}

/* Internal: strip trailing newline/carriage return */
static void strip_trailing_newline(char *str)
{
    size_t len = strlen(str);
    while (len > 0 && (str[len-1] == '\n' || str[len-1] == '\r')) {
        str[--len] = '\0';
    }
}

/* Internal: tokenize command with space delimiter */
static char *next_token(char **rest)
{
    char *token = strsep(rest, " \t");
    while (token && (*token == '\0')) {
        token = strsep(rest, " \t");
    }
    return token;
}

int bridge_init(void)
{
    struct sockaddr_un addr;
    int optval = 1;
    const char *socket_path = getenv("N88BASIC_CONTROL_SOCKET");

    /*
     * Configure the N88-BASIC ROM hook points used by the run-control bridge.
     * The runtime images bundled in this repository use the standard V2 ROM
     * addresses documented in emu.c/tests.
     */
    quasi88_set_rom_hook_addresses(0x0320, 0x04e0, 0x0600, 0x047b);

    /* Create Unix domain socket */
    control_socket_fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (control_socket_fd < 0) {
        fprintf(stderr, "Bridge: failed to create socket\n");
        return -1;
    }

    /* Set socket options */
    setsockopt(control_socket_fd, SOL_SOCKET, SO_REUSEADDR, &optval, sizeof(optval));

    /* Configure socket address */
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    if (!socket_path || socket_path[0] == '\0') {
        socket_path = default_socket_path;
    }
    strncpy(configured_socket_path, socket_path, sizeof(configured_socket_path) - 1);
    configured_socket_path[sizeof(configured_socket_path) - 1] = '\0';
    strncpy(addr.sun_path, configured_socket_path, sizeof(addr.sun_path) - 1);

    /* Remove existing socket file if present */
    unlink(configured_socket_path);

    /* Bind socket */
    if (bind(control_socket_fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        fprintf(stderr, "Bridge: failed to bind to %s\n", configured_socket_path);
        close(control_socket_fd);
        return -1;
    }

    /* Listen for connections (single client) */
    if (listen(control_socket_fd, 1) < 0) {
        fprintf(stderr, "Bridge: failed to listen\n");
        close(control_socket_fd);
        return -1;
    }

    /* Set non-blocking for accept without discarding existing flags. */
    {
        int flags = fcntl(control_socket_fd, F_GETFL, 0);
        if (flags >= 0) {
            fcntl(control_socket_fd, F_SETFL, flags | O_NONBLOCK);
        }
    }

    bridge_initialized = 1;
    bridge_only_mode = (getenv("N88BASIC_BRIDGE_ONLY") != NULL);
    bridge_reset_injected_key_events();
    bridge_reset_startup_automation();
    return RUN_ERR_OK;
}

/*
 * Command: LOAD <path> [ASCII]
 *
 * Load a BASIC file into memory.
 * Reads the BASIC source file and encodes it into memory using
 * basic_encode_list() which is the same path used by monitor_loadbas().
 */
static int cmd_load(const char *args, bridge_response_t *resp)
{
    char *path = next_token((char **)&args);
    char *fmt = next_token((char **)&args);
    int is_ascii = (!fmt || strcmp(fmt, "ASCII") == 0);

    if (!path) {
        resp->error_code = RUN_ERR_PROTOCOL;
        snprintf(resp->response, sizeof(resp->response),
                 "ERR RUN_ERR_PROTOCOL missing path argument");
        return RUN_ERR_PROTOCOL;
    }

    /* Check file existence and open */
    FILE *fp = fopen(path, "r");
    if (!fp) {
        resp->error_code = RUN_ERR_FILE_NOT_FOUND;
        snprintf(resp->response, sizeof(resp->response),
                 "ERR RUN_ERR_FILE_NOT_FOUND PATH=%s", path);
        return RUN_ERR_FILE_NOT_FOUND;
    }

    int size = 0;
    if (is_ascii) {
        size = basic_encode_list(fp);
    } else {
        size = basic_load_intermediate_code(fp);
    }
    fclose(fp);

    if (size <= 0) {
        resp->error_code = RUN_ERR_PROTOCOL;
        snprintf(resp->response, sizeof(resp->response),
                 "ERR RUN_ERR_PROTOCOL failed to encode BASIC");
        return RUN_ERR_PROTOCOL;
    }

    /* Update ROM ID - use file path (not basename heuristic) for runtime source tracking */
    strncpy(loaded_rom_id, path, sizeof(loaded_rom_id) - 1);
    loaded_rom_id[sizeof(loaded_rom_id) - 1] = '\0';
    resp->error_code = RUN_ERR_OK;
    snprintf(resp->response, sizeof(resp->response),
             "OK FILE=loaded BYTES=%d FMT=%s ROM=%s", size, fmt ? fmt : "ASCII", loaded_rom_id);

    return RUN_ERR_OK;
}

/*
 * Command: QUEUE <string>
 *
 * Queue ASCII characters for keyboard input injection.
 * Uses quasi88_keyinject() to enqueue keys for later consumption.
 *
 * The queue preserves content across MONITOR -> EXEC transitions,
 * allowing pre-loading of input before starting BASIC execution.
 */
static int cmd_queue(const char *args, bridge_response_t *resp)
{
    const char *sequence = args;
    int result;
    
    if (!sequence || *sequence == '\0') {
        /* Empty queue - inject newline */
        sequence = "<CR>";
    }
    
    if (bridge_only_mode) {
        result = bridge_enqueue_sequence_immediate(sequence);
    } else {
        result = quasi88_keyinject(sequence);
    }
    
    if (result == 0) {
        int len = strlen(sequence);
        resp->error_code = RUN_ERR_OK;
        snprintf(resp->response, sizeof(resp->response),
                 "OK QUEUED=%d", len);
        return RUN_ERR_OK;
    } else if (result == -3) {
        resp->error_code = RUN_ERR_QUEUE_FULL;
        snprintf(resp->response, sizeof(resp->response),
                 "ERR RUN_ERR_QUEUE_FULL");
        return RUN_ERR_QUEUE_FULL;
    } else if (result == -1) {
        resp->error_code = RUN_ERR_PROTOCOL;
        snprintf(resp->response, sizeof(resp->response),
                 "ERR RUN_ERR_PROTOCOL injection blocked in current mode");
        return RUN_ERR_PROTOCOL;
    } else {
        resp->error_code = RUN_ERR_PROTOCOL;
        snprintf(resp->response, sizeof(resp->response),
                 "ERR RUN_ERR_PROTOCOL bad sequence (code=%d)", result);
        return RUN_ERR_PROTOCOL;
    }
}

/*
 * Command: GO
 *
 * Start BASIC execution by entering EXEC mode.
 * Calls quasi88_exec() to transition from MONITOR to EXEC mode.
 *
 * This is the trigger that starts BASIC program execution.
 */
static int cmd_go(bridge_response_t *resp)
{
    /* Allow GO from any active runtime mode; headless --run hosts do not use monitor stdin. */
    int mode = quasi88_get_mode();
    if (mode == 0x30) {
        resp->error_code = RUN_ERR_NOT_IN_MONITOR;
        snprintf(resp->response, sizeof(resp->response),
                 "ERR RUN_ERR_NOT_IN_MONITOR mode=%d", mode);
        return RUN_ERR_NOT_IN_MONITOR;
    }

    /* Enter EXEC mode - this starts BASIC execution */
    quasi88_exec();
    startup_sequence = NULL;
    startup_sequence_pos = 0;
    startup_sequence_press_phase = 1;
    startup_sequence_hold_frames = 0;
    
    resp->error_code = RUN_ERR_OK;
    snprintf(resp->response, sizeof(resp->response),
             "OK EXEC=entering");
    
    return RUN_ERR_OK;
}

/*
 * Command: STATE
 *
 * Query current emulator state.
 * Uses quasi88_get_exec_state() and quasi88_get_mode() as source of truth.
 * Returns the loaded ROM ID from TRACKED state (set by LOAD command).
 *
 * This does NOT rely on monitor output text patterns or OCR.
 */
static int cmd_state(bridge_response_t *resp)
{
    int exec_state = quasi88_get_exec_state();
    int mode = quasi88_get_mode();

    /* Return the actual loaded ROM ID (set by LOAD command) */
    const char *rom_id = loaded_rom_id;

    resp->error_code = RUN_ERR_OK;
    snprintf(resp->response, sizeof(resp->response),
             "STATE %s MODE=%d ROM=%s",
             bridge_get_state_name(exec_state), mode, rom_id);

    return RUN_ERR_OK;
}

/*
 * Command: WAIT <timeout_ms>
 *
 * Poll until state changes or timeout occurs.
 * This uses actual emulator state transitions, not text pattern matching.
 *
 * @timeout_ms: Maximum milliseconds to wait
 *
 * Returns:
 *   STATE <new_state> ... on state change
 *   ERR RUN_ERR_TIMEOUT on timeout
 */
static int cmd_wait(const char *args, bridge_response_t *resp)
{
    int timeout_ms = atoi(args);
    if (timeout_ms <= 0) {
        timeout_ms = 5000;  /* Default timeout */
    }

    /* Get initial state */
    int initial_state = quasi88_get_exec_state();

    struct timespec start;
    clock_gettime(CLOCK_MONOTONIC, &start);

    long elapsed_ms = 0;

    /* Poll for state change */
    while (1) {
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);

        elapsed_ms = (now.tv_sec - start.tv_sec) * 1000L +
                     (now.tv_nsec - start.tv_nsec) / 1000000L;

        if (elapsed_ms >= timeout_ms) {
            resp->error_code = RUN_ERR_TIMEOUT;
            snprintf(resp->response, sizeof(resp->response),
                     "ERR RUN_ERR_TIMEOUT waited_ms=%ld", elapsed_ms);
            return RUN_ERR_TIMEOUT;
        }

        int current_state = quasi88_get_exec_state();
        if (current_state != initial_state) {
            /* State changed */
            resp->error_code = RUN_ERR_OK;
            snprintf(resp->response, sizeof(resp->response),
                     "STATE %s MODE=%d ROM=%s",
                     bridge_get_state_name(current_state), quasi88_get_mode(), loaded_rom_id);
            return RUN_ERR_OK;
        }

        /*
         * Drive the EXEC loop while waiting so the runtime can advance toward
         * INPUT_WAIT / COMPLETED / ERROR instead of pinning the bridge thread
         * in a RUNNING-only busy wait.
         */
        if (quasi88_is_exec()) {
            bridge_drive_startup_prompt_automation();
            /*
             * Drive the normal QUASI88 frame loop while waiting. Calling
             * emu_main() directly skips INIT/WAIT/screen-update transitions,
             * which leaves startup prompts and queued input stuck in bridge-only
             * sessions. The full loop preserves the runtime's normal progression.
             */
            quasi88_loop();
        } else {
            sched_yield();
        }
    }
}

/*
 * Command: QUIT
 *
 * Stop emulator and close control channel.
 */
static int cmd_quit(bridge_response_t *resp)
{
    resp->error_code = RUN_ERR_OK;
    snprintf(resp->response, sizeof(resp->response),
             "OK EXIT=0");
    
    /* Trigger emulator shutdown */
    quasi88_quit();
    
    return RUN_ERR_OK;
}

/**
 * bridge_receive_command - Receive a command from client
 *
 * Reads a command line from client_socket_fd into buf.
 * Returns number of bytes read, or -1 on error/disconnect.
 */
int bridge_receive_command(char *buf, size_t bufsize)
{
    if (client_socket_fd < 0) {
        return -1;
    }

    size_t offset = 0;
    while (offset < bufsize - 1) {
        ssize_t n = read(client_socket_fd, buf + offset, 1);
        if (n <= 0) {
            if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
                if (offset == 0) {
                    return 0;
                }
                break;
            }
            if (offset > 0) {
                break;
            }
            return -1;  /* Disconnect or error */
        }
        if (buf[offset] == '\n') {
            buf[offset] = '\0';
            return offset;
        }
        offset++;
    }
    buf[offset] = '\0';
    return offset;
}

int bridge_process_command(const char *input, bridge_response_t *resp)
{
    if (!input || !resp) {
        resp->error_code = RUN_ERR_PROTOCOL;
        snprintf(resp->response, sizeof(resp->response),
                 "ERR RUN_ERR_PROTOCOL null input or response");
        return RUN_ERR_PROTOCOL;
    }
    
    /* Make a copy we can tokenize */
    char buf[1024];
    strncpy(buf, input, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';
    strip_trailing_newline(buf);
    
    char *rest = buf;
    char *cmd = next_token(&rest);
    
    if (!cmd) {
        /* Empty command */
        resp->error_code = RUN_ERR_OK;
        snprintf(resp->response, sizeof(resp->response), "OK EMPTY");
        return RUN_ERR_OK;
    }
    
    /* Dispatch command */
    if (strcmp(cmd, "LOAD") == 0) {
        return cmd_load(rest, resp);
    } else if (strcmp(cmd, "QUEUE") == 0) {
        return cmd_queue(rest, resp);
    } else if (strcmp(cmd, "GO") == 0) {
        return cmd_go(resp);
    } else if (strcmp(cmd, "STATE") == 0) {
        return cmd_state(resp);
    } else if (strcmp(cmd, "WAIT") == 0) {
        return cmd_wait(rest, resp);
    } else if (strcmp(cmd, "TEXTSCR") == 0) {
        return cmd_textscr(resp);
    } else if (strcmp(cmd, "QUIT") == 0) {
        return cmd_quit(resp);
    } else {
        resp->error_code = RUN_ERR_PROTOCOL;
        snprintf(resp->response, sizeof(resp->response),
                 "ERR RUN_ERR_PROTOCOL unknown command '%s'", cmd);
        return RUN_ERR_PROTOCOL;
    }
}

const char *bridge_get_state_name(int state)
{
    switch (state) {
        case QUASI88_EXEC_UNKNOWN:   return "UNKNOWN";
        case QUASI88_EXEC_RUNNING:   return "RUNNING";
        case QUASI88_EXEC_READY:     return "READY";
        case QUASI88_EXEC_INPUT_WAIT: return "INPUT_WAIT";
        case QUASI88_EXEC_COMPLETED: return "COMPLETED";
        case QUASI88_EXEC_ERROR:     return "ERROR";
        default:                     return "UNKNOWN";
    }
}

const char *bridge_get_mode_name(int mode)
{
    switch (mode) {
        case 0x00:           return "EXEC";
        case 0x10:           return "MONITOR";
        case 0x20:           return "MENU";
        case 0x21:           return "PAUSE";
        case 0x30:           return "QUIT";
        default:             return "UNKNOWN";
    }
}

void bridge_cleanup(void)
{
    if (client_socket_fd >= 0) {
        close(client_socket_fd);
        client_socket_fd = -1;
    }
    if (control_socket_fd >= 0) {
        close(control_socket_fd);
        control_socket_fd = -1;
        /* Remove socket file */
        if (configured_socket_path[0] != '\0') {
            unlink(configured_socket_path);
        }
    }
    bridge_initialized = 0;
    bridge_reset_injected_key_events();
    bridge_reset_startup_automation();
    configured_socket_path[0] = '\0';
}

int bridge_drain_injected_key_event(int *code_out, int *press_flag_out)
{
    if (bridge_key_count == 0 || !code_out || !press_flag_out) {
        return FALSE;
    }

    *code_out = bridge_key_events[bridge_key_head].key_code;
    *press_flag_out = bridge_key_events[bridge_key_head].press_flag;
    bridge_key_head = (bridge_key_head + 1) % BRIDGE_KEY_EVENT_QUEUE_SIZE;
    bridge_key_count--;
    return TRUE;
}

/**
 * bridge_accept_client - Accept a client connection
 *
 * Call this from the emulator's main loop when the socket is ready.
 * Returns 0 on success, -1 on error or EAGAIN if no connection yet.
 */
int bridge_accept_client(void)
{
    if (!bridge_initialized || control_socket_fd < 0) {
        return -1;
    }
    if (client_socket_fd >= 0) {
        return 0;
    }

    struct sockaddr_un client_addr;
    socklen_t client_len = sizeof(client_addr);

    client_socket_fd = accept(control_socket_fd, 
                               (struct sockaddr *)&client_addr, 
                               &client_len);
    
    if (client_socket_fd < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK) {
            return -1;  /* No connection yet */
        }
        fprintf(stderr, "Bridge: accept failed: %s\n", strerror(errno));
        return -1;
    }

    /* Keep client socket non-blocking so monitor mode cannot stall the bridge loop. */
    {
        int flags = fcntl(client_socket_fd, F_GETFL, 0);
        if (flags >= 0) {
            fcntl(client_socket_fd, F_SETFL, flags | O_NONBLOCK);
        }
    }
    fprintf(stderr, "Bridge: client connected\n");
    return 0;
}

/**
 * bridge_send_response - Send response to client
 *
 * Sends response with line-oriented framing (newline terminated).
 * @response: bridge_response_t structure
 * Returns 0 on success, -1 on error.
 */
int bridge_send_response(const bridge_response_t *resp)
{
    if (client_socket_fd < 0) {
        return -1;
    }

    /* Write response with newline for line-oriented framing */
    ssize_t written = write(client_socket_fd, resp->response, strlen(resp->response));
    if (written < 0) {
        return -1;
    }

    /* Send newline terminator */
    const char *newline = "\n";
    if (write(client_socket_fd, newline, 1) < 0) {
        return -1;
    }

    return 0;
}

int bridge_has_client(void)
{
    return (client_socket_fd >= 0);
}

int bridge_is_bridge_only_mode(void)
{
    return bridge_only_mode;
}
