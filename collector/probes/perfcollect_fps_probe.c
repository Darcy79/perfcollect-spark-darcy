/* Own-source optional SurfaceFlinger probe. No private Binder API or injection. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#define CAP 16384
#define ROWS 512
#define LEASE UINT64_C(5000000000)
static volatile sig_atomic_t stopping;
static char token[33], directory[128], input[128];
static size_t input_n;
static uint64_t heartbeat;
static pid_t child;
static char previous[CAP+1], current[CAP+1], output[65536];
static size_t po[ROWS], pl[ROWS], co[ROWS], cl[ROWS], pn;
static uint64_t now_ns(void) {
    struct timespec t; if (clock_gettime(CLOCK_MONOTONIC, &t)) return 0;
    return (uint64_t)t.tv_sec * UINT64_C(1000000000) + (uint64_t)t.tv_nsec;
}
static void stop_signal(int sig) { (void)sig; stopping = 1; }
static void controls(void) {
    char b[128]; ssize_t n;
    while ((n=read(STDIN_FILENO,b,sizeof(b)))>0) {
        for (ssize_t i=0;i<n;i++) {
            if (b[i]=='\n') {
                input[input_n]=0;
                if (!strncmp(input,"PING ",5) && !strcmp(input+5,token)) heartbeat=now_ns();
                else if (!strncmp(input,"STOP ",5) && !strcmp(input+5,token)) stopping=1;
                else stopping=1;
                input_n=0;
            } else if (input_n+1<sizeof(input)) input[input_n++]=b[i];
            else stopping=1;
        }
    }
    if (n==0 || (n<0 && errno!=EAGAIN && errno!=EWOULDBLOCK && errno!=EINTR)) stopping=1;
}
static int alive(void) { controls(); return !stopping && now_ns()-heartbeat<LEASE; }
static int emit(const char *s, size_t len) {
    size_t at=0;
    while (at<len && alive()) {
        ssize_t n=write(STDOUT_FILENO,s+at,len-at);
        if (n>0) { at+=(size_t)n; continue; }
        if (n<0 && errno==EINTR) continue;
        if (n<0 && (errno==EAGAIN || errno==EWOULDBLOCK)) {
            struct pollfd f[2]={{STDIN_FILENO,POLLIN,0},{STDOUT_FILENO,POLLOUT,0}};
            if (poll(f,2,50)<0 && errno!=EINTR) return 0;
            if (f[1].revents&(POLLERR|POLLHUP|POLLNVAL)) return 0;
        } else return 0;
    }
    return at==len;
}
static void reap(void) {
    if (child>0) { kill(child,SIGKILL); while (waitpid(child,NULL,0)<0 && errno==EINTR) {} child=0; }
}
static void cleanup(void) {
    reap(); char path[160]; snprintf(path,sizeof(path),"%s/probe.elf",directory);
    unlink(path); rmdir(directory);
}
static int read_latency(const char *layer, size_t *size) {
#ifdef PERFCOLLECT_FPS_TEST
    const char *fixture=getenv("PERFCOLLECT_FPS_FIXTURE");
    if (fixture) {
        int fd=open(fixture,O_RDONLY|O_CLOEXEC); if(fd<0) return 0;
        ssize_t n=read(fd,current,sizeof(current)); close(fd);
        if(n<0 || n>CAP) return 0; *size=(size_t)n; return 1;
    }
#endif
    int f[2]; if (pipe2(f,O_CLOEXEC)) return 0;
    child=fork();
    if (child<0) { close(f[0]); close(f[1]); child=0; return 0; }
    if (!child) {
        prctl(PR_SET_PDEATHSIG,SIGKILL);
        if (getppid()==1) _exit(125);
        if (dup2(f[1],STDOUT_FILENO)<0) _exit(125);
        int nullfd=open("/dev/null",O_RDWR);
        if (nullfd<0 || dup2(nullfd,STDERR_FILENO)<0 || dup2(nullfd,STDIN_FILENO)<0) _exit(125);
        close(f[0]); close(f[1]); close(nullfd);
        execl("/system/bin/dumpsys","dumpsys","-t","2","SurfaceFlinger","--latency",layer,(char*)NULL);
        _exit(127);
    }
    close(f[1]);
    if (fcntl(f[0],F_SETFL,O_NONBLOCK)<0) {close(f[0]);reap();return 0;}
    uint64_t deadline=now_ns()+UINT64_C(2500000000); size_t used=0; int eof=0,ok=1,status=0;
    while (!eof && alive() && now_ns()<deadline) {
        ssize_t n=read(f[0],current+used,sizeof(current)-used);
        if(n>0) { used+=(size_t)n; if(used>CAP) {ok=0;break;} }
        else if(!n) eof=1;
        else if(errno!=EAGAIN && errno!=EINTR) {ok=0;break;}
        else {
            struct pollfd fds[2]={{STDIN_FILENO,POLLIN,0},{f[0],POLLIN,0}};
            if(poll(fds,2,20)<0 && errno!=EINTR) {ok=0;break;}
        }
    }
    close(f[0]);
    if(!eof || !alive()) {reap();return 0;}
    /* dumpsys closes stdout at exit; bounded wait also handles unusual descendants. */
    for(;;) {
        pid_t result=waitpid(child,&status,WNOHANG);
        if(result==child) break;
        if(result<0 && errno!=EINTR) {child=0;return 0;}
        if(!alive() || now_ns()>=deadline) {reap();return 0;}
        struct pollfd fds={STDIN_FILENO,POLLIN,0}; (void)poll(&fds,1,10);
    }
    child=0; *size=used;
    return ok && WIFEXITED(status) && WEXITSTATUS(status)==0;
}
static size_t split(const char *s,size_t n,size_t *offset,size_t *length) {
    size_t start=0,count=0;
    for(size_t i=0;i<n;i++) if(s[i]=='\n') {
        if(count==ROWS) return ROWS+1;
        offset[count]=start; length[count++]=i+1-start; start=i+1;
    }
    if(start<n) {if(count==ROWS)return ROWS+1;offset[count]=start;length[count++]=n-start;}
    return count;
}
static int hex_digit(char c) {return c>='0'&&c<='9'?c-'0':c>='a'&&c<='f'?c-'a'+10:-1;}
int main(int argc,char **argv) {
    if(argc!=5 || strlen(argv[1])!=32) return 2;
    for(int i=0;i<32;i++) if(hex_digit(argv[1][i])<0) return 2;
    memcpy(token,argv[1],33);
    snprintf(directory,sizeof(directory),"/data/local/tmp/perfcollect_fps_%s",token);
    if(strcmp(directory,argv[4])) return 2;
    size_t hn=strlen(argv[2]); char layer[2049];
    if(!hn || hn>4096 || hn%2) return 2;
    for(size_t i=0;i<hn/2;i++) {
        int a=hex_digit(argv[2][i*2]),b=hex_digit(argv[2][i*2+1]);
        if(a<0||b<0) return 2; layer[i]=(char)(a*16+b);
        if(!layer[i] || layer[i]=='\r' || layer[i]=='\n') return 2;
    }
    layer[hn/2]=0;
    char *end=NULL; errno=0; long period=strtol(argv[3],&end,10);
    if(errno || !argv[3][0] || *end || period<250 || period>60000) return 2;
    signal(SIGINT,stop_signal);signal(SIGTERM,stop_signal);signal(SIGPIPE,SIG_IGN);
    if(fcntl(0,F_SETFL,O_NONBLOCK)<0 || fcntl(1,F_SETFL,O_NONBLOCK)<0) return 3;
    heartbeat=now_ns(); atexit(cleanup);
    int n=snprintf(output,sizeof(output),"{\"v\":1,\"kind\":\"ready\",\"token\":\"%s\",\"seq\":0,\"device_ns\":\"%"PRIu64"\",\"clock\":\"device_monotonic\"}\n",token,now_ns());
    if(n<0 || !emit(output,(size_t)n)) return 0;
    uint64_t seq=0;
    while(alive()) {
        uint64_t t0=now_ns(); size_t bytes=0;
        if(!read_latency(layer,&bytes)) {
            n=snprintf(output,sizeof(output),"{\"v\":1,\"kind\":\"error\",\"token\":\"%s\",\"reason\":\"latency_read_failed_or_limit\"}\n",token);
            (void)emit(output,(size_t)n);break;
        }
        uint64_t t1=now_ns(); size_t cn=split(current,bytes,co,cl);
        if(cn>ROWS) break;
        n=snprintf(output,sizeof(output),"{\"v\":1,\"kind\":\"sample\",\"token\":\"%s\",\"seq\":%"PRIu64",\"base_seq\":%"PRIu64",\"t0_ns\":\"%"PRIu64"\",\"t1_ns\":\"%"PRIu64"\",\"parts\":[",token,seq+1,seq,t0,t1);
        if(n<0) break; size_t at=(size_t)n; const char hex[]="0123456789abcdef";
        for(size_t i=0;i<cn;i++) {
            if(at+cl[i]*2+32>=sizeof(output)) {stopping=1;break;}
            if(i) output[at++]=',';
            size_t j=0;for(;j<pn;j++) if(pl[j]==cl[i]&&!memcmp(previous+po[j],current+co[i],cl[i])) break;
            if(j<pn) {n=snprintf(output+at,sizeof(output)-at,"%zu",j);if(n<0){stopping=1;break;}at+=(size_t)n;}
            else {
                output[at++]='"';
                for(size_t k=0;k<cl[i];k++){unsigned char c=(unsigned char)current[co[i]+k];output[at++]=hex[c>>4];output[at++]=hex[c&15];}
                output[at++]='"';
            }
        }
        if(stopping) break;
        memcpy(output+at,"]}\n",3);at+=3;
        if(!emit(output,at)) break;
        memcpy(previous,current,bytes);memcpy(po,co,cn*sizeof(size_t));memcpy(pl,cl,cn*sizeof(size_t));pn=cn;seq++;
        uint64_t deadline=t0+(uint64_t)period*UINT64_C(1000000);
        if(deadline<=now_ns()) deadline=now_ns()+UINT64_C(50000000); /* no catch-up burst */
        while(alive()&&now_ns()<deadline) {
            struct pollfd fd={STDIN_FILENO,POLLIN,0};
            uint64_t now=now_ns(); if(now>=deadline) break;
            int delay=(int)((deadline-now)/UINT64_C(1000000));
            if(delay<1)delay=1;if(delay>50)delay=50;
            if(poll(&fd,1,delay)<0&&errno!=EINTR){stopping=1;break;}
        }
    }
    return 0;
}
