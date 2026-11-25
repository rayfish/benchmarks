// LD_PRELOAD shim: count sendmsg calls, segmented ones and their bytes; log every 20000 calls.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <netinet/in.h>
#include <stdatomic.h>
#include <stdio.h>
#include <sys/socket.h>
static atomic_long calls, seg, segbytes, bytes;
static ssize_t (*real_sendmsg)(int, const struct msghdr *, int);
ssize_t sendmsg(int fd, const struct msghdr *m, int flags) {
    if (!real_sendmsg) real_sendmsg = dlsym(RTLD_NEXT, "sendmsg");
    size_t len = 0;
    for (size_t i = 0; m && i < m->msg_iovlen; i++) len += m->msg_iov[i].iov_len;
    int s = 0;
    if (m && m->msg_controllen)
        for (struct cmsghdr *c = CMSG_FIRSTHDR(m); c; c = CMSG_NXTHDR((struct msghdr *)m, c))
            if (c->cmsg_level == 17 && c->cmsg_type == 103) s = 1;
    long n = atomic_fetch_add(&calls, 1) + 1;
    atomic_fetch_add(&bytes, len);
    if (s) { atomic_fetch_add(&seg, 1); atomic_fetch_add(&segbytes, len); }
    if (n % 20000 == 0)
        fprintf(stderr, "gso_count: calls=%ld segmented=%ld avg_bytes=%ld avg_seg_bytes=%ld\n", n, seg, bytes / n, seg ? segbytes / seg : 0);
    return real_sendmsg(fd, m, flags);
}
