"""Build the optional own-source C FPS probe with the official Android NDK."""
import argparse
from pathlib import Path
import subprocess
from build_native_cpu_probe import TARGETS


def build(ndk, output, abi="arm64-v8a", test=False):
    tools = Path(ndk) / "toolchains/llvm/prebuilt/windows-x86_64/bin"
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(tools / "clang.exe"), "--target=" + TARGETS[abi],
                    "--sysroot=" + str(tools.parent / "sysroot"), "-std=c11", "-Oz",
                    "-Wall", "-Wextra", "-Werror", "-fPIE", "-pie",
                    "-fstack-protector-strong", "-D_FORTIFY_SOURCE=2",
                    "-Wl,-z,relro,-z,now,-z,max-page-size=16384",
                    *(["-DPERFCOLLECT_FPS_TEST"] if test else []),
                    str(Path(__file__).with_name("perfcollect_fps_probe.c")), "-o", str(output)], check=True)
    subprocess.run([str(tools / "llvm-strip.exe"), "--strip-unneeded", str(output)], check=True)
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ndk", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--abi", choices=TARGETS, default="arm64-v8a")
    parser.add_argument("--test", action="store_true", help="test artifact only: permit fixture input")
    args = parser.parse_args()
    print(build(args.ndk, args.output, args.abi, args.test))
