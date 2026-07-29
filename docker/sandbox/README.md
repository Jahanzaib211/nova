# Sandbox images

Nova's default sandbox execution image (`sandbox.image` in `config.yaml`, used
by `AioSandboxProvider`) is a pre-built third-party image pulled by tag —
there is no local Dockerfile for it in this repo. `config.example.yaml`
documents that custom images should extend that default image or implement
the same AIO sandbox HTTP API.

## `Dockerfile.android`

Extends the default sandbox image with an Android build toolchain: OpenJDK
17, Android SDK cmdline-tools + platform-tools + build-tools + platform,
Gradle, and the Kotlin compiler. Without this, the agent can still write
Android/Kotlin source into the sandbox, but has no `java`, `sdkmanager`,
`gradle`, `kotlinc`, or `adb` to actually build an APK.

The Android emulator is intentionally not installed — it needs KVM hardware
acceleration unavailable in a plain Docker container, so it would install but
never run. This image supports `./gradlew assemble*` / `./gradlew build`,
not booting a virtual device.

### Build

```bash
docker build -t nova-sandbox-android:latest -f docker/sandbox/Dockerfile.android docker/sandbox/
```

Adds ~1.6GB on top of the ~10.2GB base image. Build-verified: `java`,
`javac`, `sdkmanager`, `gradle`, `kotlinc`, and `adb` all resolve inside the
built image, and a real minimal Android project builds end-to-end —
`gradle assembleDebug` (`--entrypoint bash`, since the base image's own
entrypoint boots the full sandbox supervisord stack otherwise) produced
`app/build/outputs/apk/debug/app-debug.apk`.

### Use it

Point `config.yaml`'s sandbox section at the tag you built:

```yaml
sandbox:
  use: deerflow.community.aio_sandbox:AioSandboxProvider
  image: nova-sandbox-android:latest
```

Sandbox containers are created via Docker-outside-of-Docker
(`aio_sandbox_provider.py`), so once the image exists in the local Docker
daemon, no push to a registry is required for local/dev use. For a
multi-host or provisioner (Kubernetes) deployment, push the tag to a
registry every host can pull from and reference that instead.
