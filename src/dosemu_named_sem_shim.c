#define _GNU_SOURCE

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <semaphore.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/types.h>

/*
 * NamedSem structs are allocated in MAP_SHARED|MAP_ANONYMOUS memory so that
 * the embedded sem_t is accessible from both parent and child after fork().
 * sem_init is called with pshared=1 so the kernel uses the physical (not
 * virtual) address as the futex key, enabling cross-process sem_wait/sem_post.
 *
 * Background: dosemu2.bin forks early to create a manager/dosemu process pair
 * that coordinate startup via these semaphores.  With pshared=0 and heap
 * allocation, each process gets a private futex copy after fork and deadlocks.
 */

typedef struct NamedSem {
    sem_t sem;
    char *name;
    unsigned int refcount;
    bool unlinked;
    struct NamedSem *next;
} NamedSem;

static pthread_mutex_t named_sem_lock = PTHREAD_MUTEX_INITIALIZER;
static NamedSem *named_sem_head = NULL;

static NamedSem *alloc_named_sem(void) {
    void *p = mmap(NULL, sizeof(NamedSem),
                   PROT_READ | PROT_WRITE,
                   MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) return NULL;
    memset(p, 0, sizeof(NamedSem));
    return (NamedSem *)p;
}

static void free_named_sem_mem(NamedSem *entry) {
    munmap(entry, sizeof(NamedSem));
}

static NamedSem *find_named_sem(const char *name, NamedSem **prev_out) {
    NamedSem *prev = NULL;
    NamedSem *entry = named_sem_head;

    while (entry != NULL) {
        if (strcmp(entry->name, name) == 0) {
            break;
        }
        prev = entry;
        entry = entry->next;
    }

    if (prev_out != NULL) {
        *prev_out = prev;
    }
    return entry;
}

static NamedSem *find_sem_by_ptr(sem_t *sem, NamedSem **prev_out) {
    NamedSem *prev = NULL;
    NamedSem *entry = named_sem_head;

    while (entry != NULL) {
        if (&entry->sem == sem) {
            break;
        }
        prev = entry;
        entry = entry->next;
    }

    if (prev_out != NULL) {
        *prev_out = prev;
    }
    return entry;
}

static void destroy_named_sem(NamedSem *entry, NamedSem *prev) {
    if (prev != NULL) {
        prev->next = entry->next;
    } else {
        named_sem_head = entry->next;
    }
    sem_destroy(&entry->sem);
    free(entry->name);
    free_named_sem_mem(entry);
}

sem_t *sem_open(const char *name, int oflag, ...) {
    mode_t mode = 0;
    unsigned int value = 0;
    NamedSem *entry = NULL;
    sem_t *result = SEM_FAILED;

    if ((oflag & O_CREAT) != 0) {
        va_list args;

        va_start(args, oflag);
        mode = (mode_t)va_arg(args, int);
        value = va_arg(args, unsigned int);
        va_end(args);
        (void)mode;
    }

    if (name == NULL || name[0] == '\0') {
        errno = EINVAL;
        return SEM_FAILED;
    }

    pthread_mutex_lock(&named_sem_lock);

    entry = find_named_sem(name, NULL);
    if (entry != NULL) {
        if ((oflag & O_CREAT) != 0 && (oflag & O_EXCL) != 0) {
            errno = EEXIST;
            goto done;
        }
        entry->refcount += 1;
        result = &entry->sem;
        goto done;
    }

    if ((oflag & O_CREAT) == 0) {
        errno = ENOENT;
        goto done;
    }

    entry = alloc_named_sem();
    if (entry == NULL) {
        errno = ENOMEM;
        goto done;
    }

    entry->name = strdup(name);
    if (entry->name == NULL) {
        free_named_sem_mem(entry);
        errno = ENOMEM;
        goto done;
    }

    if (sem_init(&entry->sem, 1, value) != 0) {
        int saved_errno = errno;

        free(entry->name);
        free_named_sem_mem(entry);
        errno = saved_errno;
        goto done;
    }

    entry->refcount = 1;
    entry->next = named_sem_head;
    named_sem_head = entry;
    result = &entry->sem;

done:
    pthread_mutex_unlock(&named_sem_lock);
    return result;
}

int sem_close(sem_t *sem) {
    NamedSem *entry = NULL;
    NamedSem *prev = NULL;

    pthread_mutex_lock(&named_sem_lock);
    entry = find_sem_by_ptr(sem, &prev);
    if (entry == NULL) {
        pthread_mutex_unlock(&named_sem_lock);
        errno = EINVAL;
        return -1;
    }

    if (entry->refcount > 0) {
        entry->refcount -= 1;
    }
    if (entry->refcount == 0 && entry->unlinked) {
        destroy_named_sem(entry, prev);
    }

    pthread_mutex_unlock(&named_sem_lock);
    return 0;
}

int sem_unlink(const char *name) {
    NamedSem *entry = NULL;
    NamedSem *prev = NULL;

    pthread_mutex_lock(&named_sem_lock);
    entry = find_named_sem(name, &prev);
    if (entry == NULL) {
        pthread_mutex_unlock(&named_sem_lock);
        errno = ENOENT;
        return -1;
    }

    entry->unlinked = true;
    if (entry->refcount == 0) {
        destroy_named_sem(entry, prev);
    }

    pthread_mutex_unlock(&named_sem_lock);
    return 0;
}

/*
 * Block dosemu2's fssvc (GLib/searpc RPC filesystem service) by intercepting
 * dlopen.  When the host environment lacks /dev/shm but our semaphore shim is
 * active, dosemu2 starts successfully (semaphore deadlock fixed) but the
 * searpc plugin's fssvc_add_path call fails with Transport Error — causing
 * dosemu to call leavedos(bad_rpc) before DOS can boot.
 *
 * By returning NULL for libplugin_searpc.so, dosemu2 falls back to its EMUFS
 * driver (directory-based host filesystem via /tmp/dosemu2_0/dosemu2_sh/),
 * which works correctly in sandbox environments and requires no extra IPC.
 *
 * Only active when this shim is preloaded (i.e. when the semaphore shim is
 * needed), so normal dosemu2 invocations are unaffected.
 */
void *dlopen(const char *filename, int flags) {
    static void *(*real_dlopen)(const char *, int) = NULL;
    if (!real_dlopen)
        real_dlopen = dlsym(RTLD_NEXT, "dlopen");
    if (filename && strstr(filename, "libplugin_searpc.so"))
        return NULL;
    return real_dlopen(filename, flags);
}
