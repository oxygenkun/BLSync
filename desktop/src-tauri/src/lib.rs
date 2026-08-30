use std::{
    fs,
    net::TcpListener,
    path::{Path, PathBuf},
    sync::Mutex,
    time::Duration,
};

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

struct Backend(Mutex<Option<CommandChild>>);

#[cfg(windows)]
fn kill_process_tree(child: CommandChild) {
    use std::os::windows::process::CommandExt;

    const CREATE_NO_WINDOW: u32 = 0x0800_0000;
    let pid = child.pid().to_string();
    let _ = std::process::Command::new("taskkill")
        .args(["/PID", &pid, "/T", "/F"])
        .creation_flags(CREATE_NO_WINDOW)
        .status();
}

#[cfg(not(windows))]
fn kill_process_tree(child: CommandChild) {
    let _ = child.kill();
}

fn available_port() -> Result<u16, String> {
    TcpListener::bind(("127.0.0.1", 0))
        .and_then(|listener| listener.local_addr())
        .map(|address| address.port())
        .map_err(|error| format!("无法选择后端端口: {error}"))
}

fn ensure_config(config_path: &Path) -> Result<(), String> {
    if config_path.exists() {
        return Ok(());
    }
    fs::write(config_path, include_str!("../default-config.toml"))
        .map_err(|error| format!("无法创建默认配置: {error}"))
}

fn writable_data_dir(executable_dir: &Path, fallback: PathBuf) -> PathBuf {
    let portable_dir = executable_dir.join("BLSyncData");
    let probe = portable_dir.join(format!(".write-test-{}", std::process::id()));
    let portable_is_writable = fs::create_dir_all(&portable_dir)
        .and_then(|()| {
            fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&probe)
        })
        .and_then(|_| fs::remove_file(&probe))
        .is_ok();
    if portable_is_writable {
        portable_dir
    } else {
        fallback
    }
}

async fn wait_until_ready(port: u16) -> Result<(), String> {
    let address = format!("127.0.0.1:{port}");
    for _ in 0..100 {
        if std::net::TcpStream::connect_timeout(
            &address.parse().expect("valid local address"),
            Duration::from_millis(100),
        )
        .is_ok()
        {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err("后端在 10 秒内未能启动".into())
}

fn stop_backend(app: &tauri::AppHandle) {
    if let Ok(mut backend) = app.state::<Backend>().0.lock() {
        if let Some(child) = backend.take() {
            kill_process_tree(child);
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(Backend(Mutex::new(None)))
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::CloseRequested { .. }) {
                stop_backend(window.app_handle());
            }
        })
        .setup(|app| {
            let handle = app.handle().clone();
            let executable_dir = std::env::current_exe()?
                .parent()
                .ok_or_else(|| std::io::Error::other("无法确定程序目录"))?
                .to_path_buf();
            let data_dir = writable_data_dir(&executable_dir, app.path().app_data_dir()?);
            fs::create_dir_all(data_dir.join("data"))?;
            fs::create_dir_all(data_dir.join("downloads"))?;
            let config_path = data_dir.join("config.toml");
            ensure_config(&config_path).map_err(std::io::Error::other)?;
            let port = available_port().map_err(std::io::Error::other)?;
            let inherited_path = std::env::var_os("PATH").unwrap_or_default();
            let child_path = std::env::join_paths(
                std::iter::once(executable_dir).chain(std::env::split_paths(&inherited_path)),
            )?;

            let (_events, child) = app
                .shell()
                .sidecar("blsync-backend")?
                .args(["-c", &config_path.to_string_lossy()])
                .env("BLSYNC_HOST", "127.0.0.1")
                .env("BLSYNC_PORT", port.to_string())
                .env("BLSYNC_BASE_DIR", &data_dir)
                .env("BLSYNC_DESKTOP", "1")
                .env("PATH", child_path)
                .current_dir(&data_dir)
                .spawn()?;
            *app.state::<Backend>()
                .0
                .lock()
                .expect("backend lock poisoned") = Some(child);

            tauri::async_runtime::spawn(async move {
                match wait_until_ready(port).await {
                    Ok(()) => {
                        let url = format!("http://127.0.0.1:{port}");
                        let window = WebviewWindowBuilder::new(
                            &handle,
                            "main",
                            WebviewUrl::External(url.parse().expect("valid backend URL")),
                        )
                        .title("BLSync")
                        .inner_size(1100.0, 760.0)
                        .min_inner_size(800.0, 600.0)
                        .build();
                        if let Err(error) = window {
                            eprintln!("无法创建 BLSync 窗口: {error}");
                            stop_backend(&handle);
                            handle.exit(1);
                        }
                    }
                    Err(error) => {
                        eprintln!("{error}");
                        stop_backend(&handle);
                        handle.exit(1);
                    }
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building BLSync desktop application");

    app.run(|handle, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            stop_backend(handle);
        }
    });
}
