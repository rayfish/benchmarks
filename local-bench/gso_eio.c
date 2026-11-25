// LD_PRELOAD shim: fail every sendmsg that carries UDP_SEGMENT with EIO, as
// Linux before 6.11 does when the NIC has no TX checksum offload.
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <netinet/in.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/socket.h>
#ifndef SOL_UDP
#define SOL_UDP 17
#endif
#ifndef UDP_SEGMENT
#define UDP_SEGMENT 103
#endif
static atomic_long failed;
static ssize_t (*real_sendmsg)(int, const struct msghdr *, int);
static int has_segment(const struct msghdr *m) {
    for (struct cmsghdr *c = CMSG_FIRSTHDR(m); c; c = CMSG_NXTHDR((struct msghdr *)m, c))
        if (c->cmsg_level == SOL_UDP && c->cmsg_type == UDP_SEGMENT) return 1;
    return 0;
}
ssize_t sendmsg(int fd, const struct msghdr *m, int flags) {
    if (!real_sendmsg) real_sendmsg = dlsym(RTLD_NEXT, "sendmsg");
    if (m && m->msg_controllen && has_segment(m)) {
        long n = atomic_fetch_add(&failed, 1) + 1;
        if (n == 1 || n % 1000 == 0) fprintf(stderr, "gso_eio: refused segmented send #%ld\n", n);
        errno = EIO;
        return -1;
    }
    return real_sendmsg(fd, m, flags);
}
