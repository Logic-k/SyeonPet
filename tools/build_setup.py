"""Zero-Admin Native Windows GUI Setup .exe Builder for SyeonPet.

Embeds the portable release ZIP into a standalone, native, zero-admin Windows GUI
installer compiled via the built-in Microsoft .NET Framework csc.exe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSC_PATH = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
DEFAULT_VERSION = "0.2.0"

CS_TEMPLATE = """using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace SyeonPetInstaller {
    public static class Program {
        [STAThread]
        public static int Main(string[] args) {
            bool silent = false;
            string targetDir = null;
            string desktopDir = null;
            string startMenuDir = null;
            bool noLaunch = false;
            bool noRegistry = false;

            for (int i = 0; i < args.Length; i++) {
                string arg = args[i].ToLowerInvariant();
                if (arg == "--silent" || arg == "/silent" || arg == "/s" || arg == "-s") {
                    silent = true;
                } else if ((arg == "--target-dir" || arg == "-t") && i + 1 < args.Length) {
                    targetDir = args[++i];
                } else if (arg == "--desktop-dir" && i + 1 < args.Length) {
                    desktopDir = args[++i];
                } else if (arg == "--startmenu-dir" && i + 1 < args.Length) {
                    startMenuDir = args[++i];
                } else if (arg == "--no-launch") {
                    noLaunch = true;
                } else if (arg == "--no-registry") {
                    noRegistry = true;
                }
            }

            if (string.IsNullOrEmpty(targetDir)) {
                string localApp = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
                targetDir = Path.Combine(localApp, "Programs", "SyeonPet");
            }
            if (string.IsNullOrEmpty(desktopDir)) {
                desktopDir = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            }
            if (string.IsNullOrEmpty(startMenuDir)) {
                string appData = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                startMenuDir = Path.Combine(appData, "Microsoft", "Windows", "Start Menu", "Programs");
            }

            if (silent) {
                try {
                    return InstallerEngine.PerformInstall(targetDir, desktopDir, startMenuDir, true, true, !noLaunch, null);
                } catch (Exception ex) {
                    Console.Error.WriteLine("Installation error: " + ex.Message);
                    return 1;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            using (var form = new SetupForm(targetDir, desktopDir, startMenuDir, noLaunch)) {
                Application.Run(form);
                return form.ExitCode;
            }
        }
    }

    public static class InstallerEngine {
        public static int PerformInstall(
            string targetDir,
            string desktopDir,
            string startMenuDir,
            bool makeDesktop,
            bool makeStartMenu,
            bool launchAfter,
            Action<int, string> progressCallback
        ) {
            if (progressCallback != null) progressCallback(5, "설치 준비 중...");
            Directory.CreateDirectory(targetDir);

            // Preserve existing user config.json if one already exists
            string preservedConfig = null;
            string configPath = Path.Combine(targetDir, "config.json");
            if (File.Exists(configPath)) {
                try {
                    preservedConfig = File.ReadAllText(configPath, Encoding.UTF8);
                } catch { }
            }

            // Extract embedded ZIP payload
            using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream("SyeonPetPayload.zip")) {
                if (stream == null) {
                    throw new InvalidOperationException("Embedded resource SyeonPetPayload.zip not found.");
                }
                using (ZipArchive archive = new ZipArchive(stream, ZipArchiveMode.Read)) {
                    int total = archive.Entries.Count;
                    int idx = 0;
                    foreach (ZipArchiveEntry entry in archive.Entries) {
                        idx++;
                        if (progressCallback != null && total > 0) {
                            int pct = 10 + (int)((idx * 75.0) / total);
                            progressCallback(pct, "파일 복사 중: " + entry.Name);
                        }

                        string relative = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                        string destPath = Path.Combine(targetDir, relative);

                        if (entry.FullName.EndsWith("/") || entry.FullName.EndsWith("\\\\")) {
                            Directory.CreateDirectory(destPath);
                            continue;
                        }

                        string parent = Path.GetDirectoryName(destPath);
                        if (!Directory.Exists(parent)) {
                            Directory.CreateDirectory(parent);
                        }

                        using (Stream entryStream = entry.Open())
                        using (FileStream fs = new FileStream(destPath, FileMode.Create, FileAccess.Write, FileShare.None)) {
                            entryStream.CopyTo(fs);
                        }
                    }
                }
            }

            // Restore preserved user config.json
            if (preservedConfig != null) {
                try {
                    File.WriteAllText(configPath, preservedConfig, new UTF8Encoding(false));
                } catch { }
            }

            if (progressCallback != null) progressCallback(88, "제거 스크립트 생성 중...");

            // Write clean uninstaller script
            string uninstPath = Path.Combine(targetDir, "션_삭제하기.cmd");
            string desktopLnk = Path.Combine(desktopDir, "션 데스크탑 펫.lnk");
            string startLnk = Path.Combine(startMenuDir, "션 데스크탑 펫.lnk");

            string uninstCmd = "@echo off\\r\\n" +
                              "chcp 65001 >nul\\r\\n" +
                              "taskkill /F /IM SyeonPet.exe >nul 2>&1\\r\\n" +
                              "taskkill /F /IM pythonw.exe >nul 2>&1\\r\\n" +
                              "timeout /t 1 /nobreak >nul\\r\\n" +
                              "set \\"TARGET_DIR=%~dp0\\"\\r\\n" +
                              "if exist \\"" + desktopLnk + "\\" del /f /q \\"" + desktopLnk + "\\"\\r\\n" +
                              "if exist \\"" + startLnk + "\\" del /f /q \\"" + startLnk + "\\"\\r\\n" +
                              "echo 션을 삭제하고 있습니다...\\r\\n" +
                              "cd /d \\"%TEMP%\\"\\r\\n" +
                              "rmdir /s /q \\"%TARGET_DIR%\\"\\r\\n" +
                              "echo 션 데스크탑 비서가 완전히 삭제되었습니다.\\r\\n";
            try {
                File.WriteAllText(uninstPath, uninstCmd, new UTF8Encoding(false));
            } catch { }

            if (progressCallback != null) progressCallback(94, "바로가기 생성 중...");

            string targetExe = Path.Combine(targetDir, "SyeonPet.exe");
            if (!File.Exists(targetExe)) {
                targetExe = Path.Combine(targetDir, "pythonw.exe");
            }
            string iconPath = Path.Combine(targetDir, "assets", "icon.ico");

            if (makeDesktop && Directory.Exists(desktopDir)) {
                CreateLnk(desktopLnk, targetExe, targetDir, iconPath, "션 데스크탑 펫 (Syeon Desktop Pet)");
            }
            if (makeStartMenu && Directory.Exists(startMenuDir)) {
                CreateLnk(startLnk, targetExe, targetDir, iconPath, "션 데스크탑 펫 (Syeon Desktop Pet)");
            }

            if (progressCallback != null) progressCallback(100, "설치 완료!");

            if (launchAfter && File.Exists(targetExe)) {
                try {
                    Process.Start(new ProcessStartInfo(targetExe) { WorkingDirectory = targetDir });
                } catch { }
            }

            return 0;
        }

        public static void CreateLnk(string lnkPath, string targetPath, string workDir, string iconPath, string desc) {
            try {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null) return;
                object shell = Activator.CreateInstance(shellType);
                object sc = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { lnkPath });
                Type scType = sc.GetType();
                scType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, sc, new object[] { targetPath });
                scType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, sc, new object[] { workDir });
                if (!string.IsNullOrEmpty(iconPath) && File.Exists(iconPath)) {
                    scType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, sc, new object[] { iconPath });
                }
                if (!string.IsNullOrEmpty(desc)) {
                    scType.InvokeMember("Description", BindingFlags.SetProperty, null, sc, new object[] { desc });
                }
                scType.InvokeMember("Save", BindingFlags.InvokeMethod, null, sc, null);
            } catch { }
        }
    }

    public class SetupForm : Form {
        public int ExitCode = 0;
        private string _targetDir;
        private string _desktopDir;
        private string _startMenuDir;
        private bool _noLaunch;

        private ProgressBar _progressBar;
        private Label _statusLabel;
        private Button _btnInstall;
        private Button _btnClose;
        private CheckBox _chkDesktop;
        private CheckBox _chkStartMenu;
        private CheckBox _chkLaunch;
        private TextBox _txtDir;

        public SetupForm(string targetDir, string desktopDir, string startMenuDir, bool noLaunch) {
            _targetDir = targetDir;
            _desktopDir = desktopDir;
            _startMenuDir = startMenuDir;
            _noLaunch = noLaunch;

            Text = "🌱 션 데스크탑 비서 v__VERSION__ 간편 설치";
            Width = 530;
            Height = 440;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Malgun Gothic", 9.5f, FontStyle.Regular);
            BackColor = Color.FromArgb(248, 250, 252);

            // Header Banner
            var header = new Panel {
                Dock = DockStyle.Top,
                Height = 78,
                BackColor = Color.FromArgb(224, 242, 254)
            };
            var title = new Label {
                Text = "🌱 션 데스크탑 비서 v__VERSION__",
                Font = new Font("Malgun Gothic", 12.5f, FontStyle.Bold),
                ForeColor = Color.FromArgb(15, 23, 42),
                Location = new Point(18, 14),
                AutoSize = true
            };
            var subtitle = new Label {
                Text = "바탕화면에서 만나는 귀여운 AI 비서 션을 간편하게 설치합니다 (관리자 권한 불필요).",
                Font = new Font("Malgun Gothic", 9.0f, FontStyle.Regular),
                ForeColor = Color.FromArgb(51, 65, 85),
                Location = new Point(19, 44),
                AutoSize = true
            };
            header.Controls.Add(title);
            header.Controls.Add(subtitle);
            Controls.Add(header);

            // Main Controls Panel
            int y = 92;
            var lblDir = new Label {
                Text = "설치 폴더:",
                Location = new Point(20, y),
                AutoSize = true
            };
            Controls.Add(lblDir);

            y += 24;
            _txtDir = new TextBox {
                Text = _targetDir,
                Location = new Point(20, y),
                Width = 475,
                ReadOnly = true,
                BackColor = Color.White
            };
            Controls.Add(_txtDir);

            y += 36;
            _chkDesktop = new CheckBox {
                Text = "바탕화면에 '션 데스크탑 펫' 바로가기 만들기",
                Location = new Point(22, y),
                AutoSize = true,
                Checked = true
            };
            Controls.Add(_chkDesktop);

            y += 26;
            _chkStartMenu = new CheckBox {
                Text = "시작 메뉴에 등록하기",
                Location = new Point(22, y),
                AutoSize = true,
                Checked = true
            };
            Controls.Add(_chkStartMenu);

            y += 26;
            _chkLaunch = new CheckBox {
                Text = "설치 완료 후 바로 션 만나기",
                Location = new Point(22, y),
                AutoSize = true,
                Checked = !_noLaunch
            };
            Controls.Add(_chkLaunch);

            y += 34;
            _progressBar = new ProgressBar {
                Location = new Point(20, y),
                Width = 475,
                Height = 22,
                Minimum = 0,
                Maximum = 100,
                Value = 0
            };
            Controls.Add(_progressBar);

            y += 26;
            _statusLabel = new Label {
                Text = "준비 완료. [설치 시작] 버튼을 눌러주세요.",
                Location = new Point(20, y),
                Width = 475,
                ForeColor = Color.FromArgb(71, 85, 105)
            };
            Controls.Add(_statusLabel);

            // Buttons
            _btnInstall = new Button {
                Text = "설치 시작",
                Location = new Point(300, 350),
                Width = 100,
                Height = 34,
                BackColor = Color.FromArgb(25, 91, 131),
                ForeColor = Color.White,
                FlatStyle = FlatStyle.Flat,
                Font = new Font("Malgun Gothic", 9.5f, FontStyle.Bold)
            };
            _btnInstall.Click += OnInstallClick;
            Controls.Add(_btnInstall);

            _btnClose = new Button {
                Text = "닫기",
                Location = new Point(410, 350),
                Width = 85,
                Height = 34,
                BackColor = Color.FromArgb(241, 245, 249),
                ForeColor = Color.FromArgb(15, 23, 42),
                FlatStyle = FlatStyle.Flat
            };
            _btnClose.Click += (s, e) => Close();
            Controls.Add(_btnClose);
        }

        private void OnInstallClick(object sender, EventArgs e) {
            if (_btnInstall.Text == "완료") {
                Close();
                return;
            }

            _btnInstall.Enabled = false;
            _btnClose.Enabled = false;
            _chkDesktop.Enabled = false;
            _chkStartMenu.Enabled = false;
            _chkLaunch.Enabled = false;

            ThreadPool.QueueUserWorkItem(_ => {
                try {
                    InstallerEngine.PerformInstall(
                        _targetDir,
                        _desktopDir,
                        _startMenuDir,
                        _chkDesktop.Checked,
                        _chkStartMenu.Checked,
                        _chkLaunch.Checked,
                        (pct, msg) => {
                            Invoke((Action)(() => {
                                _progressBar.Value = Math.Min(100, Math.Max(0, pct));
                                _statusLabel.Text = msg;
                            }));
                        }
                    );
                    Invoke((Action)(() => {
                        _progressBar.Value = 100;
                        _statusLabel.Text = "설치가 완료되었습니다! 언제든 바탕화면의 션을 찾아주세요.";
                        _statusLabel.ForeColor = Color.FromArgb(22, 101, 52);
                        _btnInstall.Text = "완료";
                        _btnInstall.Enabled = true;
                        _btnClose.Enabled = true;
                    }));
                } catch (Exception ex) {
                    Invoke((Action)(() => {
                        _statusLabel.Text = "설치 중 오류 발생: " + ex.Message;
                        _statusLabel.ForeColor = Color.FromArgb(185, 28, 28);
                        _btnInstall.Enabled = true;
                        _btnClose.Enabled = true;
                        ExitCode = 1;
                    }));
                }
            });
        }
    }
}
"""


def build_setup(zip_path: Path, output_exe: Path, version: str = DEFAULT_VERSION) -> dict:
    zip_path = Path(zip_path).resolve()
    output_exe = Path(output_exe).resolve()

    if not zip_path.is_file() or not zipfile.is_zipfile(zip_path):
        raise FileNotFoundError(f"유효한 ZIP 파일을 찾을 수 없습니다: {zip_path}")

    if output_exe.suffix.lower() != ".exe":
        raise ValueError(f"출력 경로는 .exe 파일이어야 합니다: {output_exe}")

    if output_exe.is_relative_to(ROOT):
        raise ValueError(f"출력 파일은 저장소({ROOT}) 바깥에 있어야 합니다: {output_exe}")

    if not output_exe.parent.is_dir():
        output_exe.parent.mkdir(parents=True, exist_ok=True)

    # Verify manifest inside zip
    with zipfile.ZipFile(zip_path) as zf:
        if "package-manifest.json" not in zf.namelist():
            raise ValueError("ZIP 파일에 package-manifest.json이 없습니다.")
        manifest = json.loads(zf.read("package-manifest.json"))
        zip_ver = manifest.get("version")
        if zip_ver != version:
            raise ValueError(f"ZIP 버전({zip_ver})과 요청 버전({version})이 일치하지 않습니다.")

    if not CSC_PATH.is_file():
        raise FileNotFoundError(f"C# 컴파일러를 찾을 수 없습니다: {CSC_PATH}")

    icon_path = ROOT / "assets" / "icon.ico"

    cs_code = CS_TEMPLATE.replace("__VERSION__", version)

    with tempfile.TemporaryDirectory(prefix="syeon-setup-build-") as tmpdir:
        cs_file = Path(tmpdir) / "SetupProgram.cs"
        cs_file.write_text(cs_code, encoding="utf-8")

        cmd = [
            str(CSC_PATH),
            "-nologo",
            "-optimize+",
            "-utf8output",
            "/target:winexe",
            f"/resource:{zip_path},SyeonPetPayload.zip",
            "/reference:System.Windows.Forms.dll",
            "/reference:System.Drawing.dll",
            "/reference:System.IO.Compression.dll",
            "/reference:System.IO.Compression.FileSystem.dll",
            f"/out:{output_exe}",
        ]
        if icon_path.is_file():
            cmd.append(f"/win32icon:{icon_path}")
        cmd.append(str(cs_file))

        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode != 0:
            raise RuntimeError(f"Setup exe 컴파일 실패: rc={res.returncode}\\nstdout={res.stdout}\\nstderr={res.stderr}")

    # Compute checksum
    sha256 = hashlib.sha256(output_exe.read_bytes()).hexdigest()
    checksum_file = Path(str(output_exe) + ".sha256")
    checksum_file.write_text(f"{sha256}  {output_exe.name}\n", encoding="utf-8", newline="\n")

    return {
        "output": str(output_exe),
        "sha256": sha256,
        "size": output_exe.stat().st_size,
        "version": version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", required=True, type=Path, help="Path to SyeonPet-version-windows.zip")
    parser.add_argument("--output", required=True, type=Path, help="Path to output Setup exe")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="Release version")
    args = parser.parse_args()

    try:
        result = build_setup(args.zip, args.output, args.version)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"빌드 실패: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
