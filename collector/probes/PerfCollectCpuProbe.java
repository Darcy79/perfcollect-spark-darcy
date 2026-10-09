import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;

/** Own-source, shell-UID CPU counter probe. No app injection or network use. */
public final class PerfCollectCpuProbe {
    private static volatile long heartbeat = System.nanoTime();
    private static volatile boolean running = true;
    private static final long LEASE_NS = 5_000_000_000L;
    private static final String ROOT = System.getProperty("perfcollect.procRoot", "/proc");

    private static String read(String path) throws IOException {
        byte[] bytes = Files.readAllBytes(Paths.get(ROOT + path));
        if (bytes.length > 1048576) throw new IOException("oversize_proc_file");
        return new String(bytes, StandardCharsets.UTF_8);
    }
    private static String hex(String value) {
        StringBuilder out = new StringBuilder();
        for (byte b : value.getBytes(StandardCharsets.UTF_8)) out.append(String.format("%02x", b & 255));
        return out.toString();
    }
    private static String[] stat(int pid) throws IOException {
        String raw = read("/" + pid + "/stat");
        int end = raw.lastIndexOf(')');
        if (end < 0 || !raw.startsWith(pid + " (")) throw new IOException("bad_stat");
        String[] fields = raw.substring(end + 1).trim().split("\\s+");
        if (fields.length < 20) throw new IOException("short_stat");
        return fields;
    }
    private static String prefix(String kind, String token, long seq) {
        return "{\"v\":1,\"kind\":\"" + kind + "\",\"token\":\"" + token + "\",\"seq\":" + seq;
    }
    private static void emit(String value) throws IOException {
        System.out.println(value);
        System.out.flush();
        if (System.out.checkError()) throw new IOException("stdout_closed");
    }
    public static void main(String[] args) throws Exception {
        if (args.length != 5) throw new IllegalArgumentException("token pid name_hex interval_ms directory");
        final String token = args[0];
        final int pid = Integer.parseInt(args[1]);
        final String expected = args[2];
        final int interval = Integer.parseInt(args[3]);
        final String directory = args[4];
        if (!token.matches("[0-9a-f]{32}") || pid <= 0 || !expected.matches("(?:[0-9a-f]{2}){1,512}")
                || interval < 250 || interval > 60000
                || !directory.equals("/data/local/tmp/perfcollect_cpu_" + token))
            throw new IllegalArgumentException("invalid_arguments");
        // Only our exact nonce-owned files, never recursive cleanup or PID kill.
        if (ROOT.equals("/proc")) Runtime.getRuntime().addShutdownHook(new Thread(() -> {
            new File(directory, "probe.dex").delete();
            new File(directory).delete();
        }));
        Thread control = new Thread(() -> {
            try (BufferedReader input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8))) {
                String line;
                while ((line = input.readLine()) != null) {
                    if (line.equals("PING " + token)) heartbeat = System.nanoTime();
                    else if (line.equals("STOP " + token)) break;
                    else break;
                }
            } catch (IOException ignored) { }
            running = false;
            System.exit(0);
        }, "probe-control");
        control.setDaemon(true);
        control.start();
        Thread watchdog = new Thread(() -> {
            while (running) {
                if (System.nanoTime() - heartbeat > LEASE_NS) System.exit(0);
                try { Thread.sleep(100); } catch (InterruptedException ignored) { return; }
            }
        }, "probe-watchdog");
        watchdog.setDaemon(true);
        watchdog.start();
        emit(prefix("ready", token, 0) + ",\"device_ns\":\"" + System.nanoTime()
                + "\",\"clock\":\"device_monotonic\",\"pid\":" + pid + "}");
        long seq = 0;
        while (running) {
            long started = System.nanoTime();
            try {
                String[] first = stat(pid);
                String name = read("/" + pid + "/cmdline").split("\u0000", 2)[0];
                if (!hex(name).equals(expected)) throw new IOException("identity_mismatch");
                String cpu = read("/stat").split("\n", 2)[0];
                String[] fields = cpu.trim().split("\\s+");
                if (!fields[0].equals("cpu") || fields.length < 5) throw new IOException("bad_cpu");
                long total = 0;
                for (int i = 1; i < fields.length; i++) total += Long.parseLong(fields[i]);
                long idle = Long.parseLong(fields[4]) + (fields.length > 5 ? Long.parseLong(fields[5]) : 0);
                String[] last = stat(pid);
                String nameAfter = read("/" + pid + "/cmdline").split("\u0000", 2)[0];
                if (!first[19].equals(last[19]) || !name.equals(nameAfter))
                    throw new IOException("identity_changed");
                long ticks = Long.parseLong(last[11]) + Long.parseLong(last[12]);
                long finished = System.nanoTime();
                emit(prefix("sample", token, ++seq) + ",\"pid\":" + pid
                    + ",\"name_hex\":\"" + expected + "\",\"start_ticks\":\"" + last[19]
                    + "\",\"proc_ticks\":\"" + ticks + "\",\"total\":\"" + total
                    + "\",\"busy\":\"" + (total-idle) + "\",\"t0_ns\":\"" + started
                    + "\",\"t1_ns\":\"" + finished + "\"}");
            } catch (Exception error) {
                emit(prefix("error", token, ++seq) + ",\"reason\":\"read_or_identity_failed\"}");
                break;
            }
            long remaining = interval * 1000000L - (System.nanoTime() - started);
            if (remaining > 0) Thread.sleep(remaining / 1000000L, (int)(remaining % 1000000L));
        }
    }
}
