"""Build our optional probe; JDK and Android SDK are build-time dependencies only."""
import argparse
from pathlib import Path
import subprocess
import tempfile


def build(jdk, sdk, build_tools, platform, output):
    jdk, sdk, output = Path(jdk), Path(sdk), Path(output)
    java = jdk / "bin" / "java.exe"
    javac = jdk / "bin" / "javac.exe"
    d8 = sdk / "build-tools" / build_tools / "lib" / "d8.jar"
    android = sdk / "platforms" / platform / "android.jar"
    for path in (java, javac, d8, android):
        if not path.is_file():
            raise FileNotFoundError(path)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run([str(javac), "--release", "8", "-encoding", "UTF-8", "-d", folder,
                        str(Path(__file__).with_name("PerfCollectCpuProbe.java"))], check=True)
        classes = [str(p) for p in Path(folder).glob("*.class")]
        subprocess.run([str(java), "-cp", str(d8), "com.android.tools.r8.D8", "--min-api", "26",
                        "--lib", str(android), "--output", str(output), *classes], check=True)
    return output / "classes.dex"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("jdk", "sdk", "build-tools", "platform", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    print(build(args.jdk, args.sdk, args.build_tools, args.platform, args.output))
