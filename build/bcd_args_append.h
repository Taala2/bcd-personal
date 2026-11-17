/* madeira-bcd personal (2026-11-07): env.MADEIRA_ARGS_APPEND = "<program> <arguments...>".
 *
 * Appends arguments to the command line of the program BCD itself starts (argv[1] of wine), when that
 * program is <program> (case-insensitive file name). A game started inside the Wine desktop is started
 * by explorer.exe with the game's path among its arguments: then a match there counts, and the arguments
 * land at the end, which explorer hands to the game. Any other program (BCD Dock, an installer, a Steam
 * game started through Dock) gets nothing.
 *
 * Why: Chromium reads switches such as --in-process-gpu in its first (browser) process. A personal engine
 * rule can set env lines but not a library entry's launch arguments, and MADEIRA_CHILD_ARGS reaches only
 * the processes the game starts. V.0.7 (NW.js) with "--in-process-gpu
 * --disable-features=AudioServiceOutOfProcess --enable-features=NetworkServiceInProcess2" ran 8 copies of
 * nw.dll instead of 12 and peaked at 5091 MB instead of 6141 (FINDINGS.md 5f).
 *
 * Tokens split on spaces and tabs; double quotes group a token and are dropped, as for MADEIRA_ARGS. A
 * token already on the command line (the player's own launch arguments) is not added twice. At most
 * `max` arguments in all. Pure C, no allocation: the tokens are written into `buf`. Returns the number of
 * arguments appended; *why says what was decided, for the log. */
#ifndef BCD_ARGS_APPEND_H
#define BCD_ARGS_APPEND_H

#include <string.h>
#include <strings.h>

static const char *bcd_args_append_base(const char *path)
{
    const char *b = path;
    for (const char *p = path; *p; p++)
        if (*p == '\\' || *p == '/') b = p + 1;
    return b;
}

/* Splits `src` into `buf` (tokens NUL-separated) and fills tok[]; returns the count. */
static int bcd_args_append_split(const char *src, char *buf, size_t bufsz, char **tok, int maxtok)
{
    int n = 0;
    size_t len = strlen(src);
    if (!bufsz) return 0;
    if (len > bufsz - 1) len = bufsz - 1;
    memcpy(buf, src, len);
    buf[len] = 0;
    char *r = buf, *w = buf;   /* w never passes r: tokens only shrink */
    while (*r && n < maxtok) {
        while (*r == ' ' || *r == '\t') r++;
        if (!*r) break;
        tok[n++] = w;
        int quoted = 0;
        while (*r && (quoted || (*r != ' ' && *r != '\t'))) {
            if (*r == '"') { quoted = !quoted; r++; continue; }
            *w++ = *r++;
        }
        if (*r) r++;
        *w++ = 0;
    }
    return n;
}

static int bcd_args_append_apply(const char *exe_path, char **argv_extra, int *argc_extra, int max,
                                 const char *spec, char *buf, size_t bufsz, const char **why)
{
    char *tok[64];
    int ntok, added = 0;
    const char *target = bcd_args_append_base(exe_path);

    *why = "unset";
    if (!spec || !*spec) return 0;
    ntok = bcd_args_append_split(spec, buf, bufsz, tok, 64);
    if (ntok < 2) { *why = "no arguments after the program name"; return 0; }
    if (!strcasecmp(target, "explorer.exe")) {
        /* the Wine desktop: explorer /desktop=... <game path> [the game's own arguments] */
        int found = 0;
        for (int j = 0; j < *argc_extra && !found; j++)
            found = !strcasecmp(bcd_args_append_base(argv_extra[j]), tok[0]);
        if (!found) { *why = "another program"; return 0; }
    } else if (strcasecmp(target, tok[0])) { *why = "another program"; return 0; }
    for (int i = 1; i < ntok && *argc_extra < max; i++) {
        int dup = 0;
        for (int j = 0; j < *argc_extra && !dup; j++) dup = !strcmp(argv_extra[j], tok[i]);
        if (dup) continue;
        argv_extra[(*argc_extra)++] = tok[i];
        added++;
    }
    *why = added ? "appended" : "already on the command line";
    return added;
}

#endif
