"""Build the standalone C probe using the official Android NDK (no runtime JDK)."""
import argparse
from pathlib import Path
import subprocess

TARGETS = {"arm64-v8a": "aarch64-linux-android26", "armeabi-v7a": "armv7a-linux-androideabi26"}

def build(ndk, output, abi="arm64-v8a", test_root=False):
    tools = Path(ndk) / "toolchains/llvm/prebuilt/windows-x86_64/bin"
    compiler, strip = tools / "clang.exe", tools / "llvm-strip.exe"
    for path in (compiler, strip):
        if not path.is_file():
            raise FileNotFoundError(path)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = ["-DPERFCOLLECT_TEST_ROOT"] if test_root else []
    subprocess.run([str(compiler), "--target=" + TARGETS[abi], "--sysroot=" + str(tools.parent / "sysroot"),
                    "-std=c11", "-Oz", "-Wall", "-Wextra", "-Werror", "-fPIE", "-pie",
                    "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2", "-Wl,-z,relro,-z,now,-z,max-page-size=16384",
                    *flags, str(Path(__file__).with_name("perfcollect_cpu_probe.c")), "-o", str(output)], check=True)
    subprocess.run([str(strip), "--strip-unneeded", str(output)], check=True)
    return output

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ndk", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--abi", choices=TARGETS, default="arm64-v8a")
    parser.add_argument("--test-root", action="store_true", help="test artifact only: allow a synthetic proc root")
    args = parser.parse_args()
    print(build(args.ndk, args.output, args.abi, args.test_root))
