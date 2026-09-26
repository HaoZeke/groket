/*
 * groket._listwalk - Limited API session discovery.
 *
 * Heap module (PEP 489). POSIX opendir/readdir/stat only. No PyObject
 * internals, no PyList_GET_ITEM.
 */
#define Py_LIMITED_API 0x030D0000
#include <Python.h>

#include <dirent.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>

static const char *const SKIP_DIRS[] = {
    "groket-plugins",
    "groket-skills",
    "subagents",
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "target",
    "dist",
    "build",
    ".cache",
    ".tox",
    ".groket-resume-seed",
    ".groket-workspace-seed",
    "workspace",
    NULL,
};

typedef struct {
    char **items;
    Py_ssize_t n;
    Py_ssize_t cap;
} PathStack;

static int
is_skip_name(const char *name)
{
    size_t i;
    size_t n;

    for (i = 0; SKIP_DIRS[i] != NULL; i++) {
        if (strcmp(name, SKIP_DIRS[i]) == 0) {
            return 1;
        }
    }
    n = strlen(name);
    return (n >= 6 && strcmp(name + (n - 6), ".stage") == 0);
}

static int
path_has_subagents(const char *path)
{
    const char *p = path;

    while (*p != '\0') {
        const char *start;
        size_t n;

        while (*p == '/') {
            p++;
        }
        if (*p == '\0') {
            break;
        }
        start = p;
        while (*p != '\0' && *p != '/') {
            p++;
        }
        n = (size_t)(p - start);
        if (n == 9 && memcmp(start, "subagents", 9) == 0) {
            return 1;
        }
    }
    return 0;
}

static char *
join_path(const char *dir, const char *name)
{
    size_t dlen = strlen(dir);
    size_t nlen = strlen(name);
    int slash = (dlen > 0 && dir[dlen - 1] != '/');
    size_t total = dlen + nlen + (slash ? 2 : 1);
    char *out = (char *)PyMem_Malloc(total);

    if (out == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    memcpy(out, dir, dlen);
    if (slash) {
        out[dlen] = '/';
        memcpy(out + dlen + 1, name, nlen + 1);
    } else {
        memcpy(out + dlen, name, nlen + 1);
    }
    return out;
}

static char *
copy_cstr(const char *s)
{
    size_t n = strlen(s) + 1;
    char *out = (char *)PyMem_Malloc(n);

    if (out == NULL) {
        PyErr_NoMemory();
        return NULL;
    }
    memcpy(out, s, n);
    return out;
}

static int
marker_present(const char *path)
{
    struct stat st;

    if (lstat(path, &st) != 0) {
        return 0;
    }
    return !S_ISDIR(st.st_mode);
}

static int
events_nonempty(const char *path)
{
    struct stat st;

    if (lstat(path, &st) != 0) {
        return 0;
    }
    if (S_ISDIR(st.st_mode)) {
        return 0;
    }
    if (stat(path, &st) != 0) {
        return 0;
    }
    return st.st_size > 0;
}

static int
dir_is_session(const char *path)
{
    static const char *const names[] = {
        "updates.jsonl",
        "summary.json",
        "events.jsonl",
        NULL,
    };
    size_t i;

    for (i = 0; names[i] != NULL; i++) {
        char *fp = join_path(path, names[i]);
        int hit;

        if (fp == NULL) {
            return -1;
        }
        if (strcmp(names[i], "events.jsonl") == 0) {
            hit = events_nonempty(fp);
        } else {
            hit = marker_present(fp);
        }
        PyMem_Free(fp);
        if (hit) {
            return 1;
        }
    }
    return 0;
}

static int
entry_is_real_dir(const char *path)
{
    struct stat st;

    if (lstat(path, &st) != 0) {
        return 0;
    }
    return S_ISDIR(st.st_mode);
}

static int
stack_push(PathStack *st, char *owned)
{
    if (st->n == st->cap) {
        Py_ssize_t ncap = (st->cap == 0) ? 16 : st->cap * 2;
        char **ni = (char **)PyMem_Realloc(st->items, (size_t)ncap * sizeof(char *));

        if (ni == NULL) {
            PyErr_NoMemory();
            return -1;
        }
        st->items = ni;
        st->cap = ncap;
    }
    st->items[st->n++] = owned;
    return 0;
}

static void
stack_clear(PathStack *st)
{
    Py_ssize_t i;

    for (i = 0; i < st->n; i++) {
        PyMem_Free(st->items[i]);
    }
    PyMem_Free(st->items);
    st->items = NULL;
    st->n = 0;
    st->cap = 0;
}

static int
append_path(PyObject *out, const char *path)
{
    PyObject *s = PyUnicode_DecodeFSDefault(path);

    if (s == NULL) {
        return -1;
    }
    if (PyList_Append(out, s) < 0) {
        Py_DECREF(s);
        return -1;
    }
    Py_DECREF(s);
    return 0;
}

static int
walk_children(const char *path, PathStack *st)
{
    DIR *d = opendir(path);
    struct dirent *ent;

    if (d == NULL) {
        return 0;
    }
    while ((ent = readdir(d)) != NULL) {
        char *child;

        if (strcmp(ent->d_name, ".") == 0 || strcmp(ent->d_name, "..") == 0) {
            continue;
        }
        if (is_skip_name(ent->d_name)) {
            continue;
        }
        child = join_path(path, ent->d_name);
        if (child == NULL) {
            closedir(d);
            return -1;
        }
        if (!entry_is_real_dir(child)) {
            PyMem_Free(child);
            continue;
        }
        if (stack_push(st, child) < 0) {
            PyMem_Free(child);
            closedir(d);
            return -1;
        }
    }
    closedir(d);
    return 0;
}

static int
walk_root(const char *root, PyObject *out)
{
    PathStack st = {NULL, 0, 0};
    char *owned = copy_cstr(root);
    int rc = 0;

    if (owned == NULL) {
        return -1;
    }
    if (stack_push(&st, owned) < 0) {
        PyMem_Free(owned);
        stack_clear(&st);
        return -1;
    }

    while (st.n > 0) {
        char *path = st.items[--st.n];
        int sess;

        if (path_has_subagents(path)) {
            PyMem_Free(path);
            continue;
        }
        sess = dir_is_session(path);
        if (sess < 0) {
            PyMem_Free(path);
            rc = -1;
            break;
        }
        if (sess) {
            if (append_path(out, path) < 0) {
                PyMem_Free(path);
                rc = -1;
                break;
            }
            PyMem_Free(path);
            continue;
        }
        if (walk_children(path, &st) < 0) {
            PyMem_Free(path);
            rc = -1;
            break;
        }
        PyMem_Free(path);
    }
    stack_clear(&st);
    return rc;
}

PyDoc_STRVAR(
    find_sessions_doc,
    "find_sessions(root)\n"
    "--\n"
    "\n"
    "Return paths of session directories under root.\n"
    "\n"
    "A session directory contains updates.jsonl or summary.json, or a\n"
    "non-empty events.jsonl. Skip names and *.stage directories are not\n"
    "descended into. Once a session is found, its children are not walked.\n"
    "Directory symlinks are not followed. Missing paths and OS errors\n"
    "return an empty list.\n");

static PyObject *
listwalk_find_sessions(PyObject *self, PyObject *arg)
{
    PyObject *fs;
    const char *root;
    PyObject *out;
    int rc;

    (void)self;
    fs = PyUnicode_EncodeFSDefault(arg);
    if (fs == NULL) {
        return NULL;
    }
    root = PyBytes_AsString(fs);
    if (root == NULL) {
        Py_DECREF(fs);
        return NULL;
    }
    out = PyList_New(0);
    if (out == NULL) {
        Py_DECREF(fs);
        return NULL;
    }
    rc = walk_root(root, out);
    Py_DECREF(fs);
    if (rc < 0) {
        Py_DECREF(out);
        return NULL;
    }
    return out;
}

PyDoc_STRVAR(
    looks_like_doc,
    "looks_like_session_dir(path)\n"
    "--\n"
    "\n"
    "True if path looks like a Grok session directory.\n"
    "\n"
    "True when the directory contains updates.jsonl or summary.json, or\n"
    "events.jsonl with size greater than zero. Missing paths and OS\n"
    "errors return False.\n");

static PyObject *
listwalk_looks_like_session_dir(PyObject *self, PyObject *arg)
{
    PyObject *fs;
    const char *path;
    int hit;

    (void)self;
    fs = PyUnicode_EncodeFSDefault(arg);
    if (fs == NULL) {
        return NULL;
    }
    path = PyBytes_AsString(fs);
    if (path == NULL) {
        Py_DECREF(fs);
        return NULL;
    }
    hit = dir_is_session(path);
    Py_DECREF(fs);
    if (hit < 0) {
        return NULL;
    }
    if (hit) {
        Py_RETURN_TRUE;
    }
    Py_RETURN_FALSE;
}

static PyMethodDef listwalk_methods[] = {
    {"find_sessions", listwalk_find_sessions, METH_O, find_sessions_doc},
    {"looks_like_session_dir", listwalk_looks_like_session_dir, METH_O, looks_like_doc},
    {NULL, NULL, 0, NULL},
};

static int
listwalk_exec(PyObject *module)
{
    (void)module;
    return 0;
}

static PyModuleDef_Slot listwalk_slots[] = {
    {Py_mod_exec, listwalk_exec},
    {0, NULL},
};

PyDoc_STRVAR(
    module_doc,
    "Limited API helpers for discovering Grok session directories.\n"
    "\n"
    "Walks a traces tree with POSIX opendir/readdir/stat and does not\n"
    "follow directory symlinks. Missing paths and OS errors yield empty\n"
    "results rather than exceptions.\n");

static struct PyModuleDef listwalk_module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "groket._listwalk",
    .m_doc = module_doc,
    .m_size = 0,
    .m_methods = listwalk_methods,
    .m_slots = listwalk_slots,
};

PyMODINIT_FUNC
PyInit__listwalk(void)
{
    return PyModuleDef_Init(&listwalk_module);
}
