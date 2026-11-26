import AppKit
import Combine
import Darwin
import Foundation
import SwiftUI

// MARK: - Snapshot Models

struct HostStatusSnapshot: Decodable {
    let online: Bool
    let pid: Int?
    let auto_dream: Bool
    let teams_away_only: Bool
    let teams_inbox: Bool
}

struct TotalsSnapshot: Decodable {
    let today_hits: Int
    let total_hits: Int
    let memos: Int
    let claims: Int
    let solutions: Int
    let themes: Int
    let open_sessions: Int
    let active_sessions: Int
}

struct SessionKnowledgeItem: Decodable, Identifiable {
    var id: String { "\(kind):\(subject)" }
    let kind: String
    let subject: String
    let title: String
    let event: String
    let time_ago: String
}

struct LiveSessionItem: Decodable, Identifiable {
    var id: String { session }
    let session: String
    let session_short: String
    let client: String
    let workspace: String
    let title: String
    let state: String
    let last_query: String
    let knowledge_used: [SessionKnowledgeItem]
    let updated_ago: String
}

struct ActivityFeedItem: Decodable, Identifiable {
    let id: String
    let event_type: String
    let kind: String
    let badge: String
    let action: String
    let title: String
    let subject: String
    let workspace: String
    let session_short: String
    let task_title: String
    let occurred_at: String
    let time_ago: String
}

struct MenuBarSnapshot: Decodable {
    let schema: String
    let updated_at: String
    let machine_name: String
    let primary_workspace: String?
    let just_hit_knowledge: Bool
    let last_hit_ago: String
    let host: HostStatusSnapshot
    let totals: TotalsSnapshot
    let live_sessions: [LiveSessionItem]
    let recent_activity: [ActivityFeedItem]
}

// MARK: - CLI Configuration

struct RuntimeCLI {
    let homePath: String
    let pythonBin: String
    let llrScript: String

    static func parseFromCommandLine() -> RuntimeCLI {
        let args = CommandLine.arguments
        var home = (NSHomeDirectory() as NSString).appendingPathComponent(".local-learning-runtime")
        var py = "/usr/bin/python3"
        var llr = ""
        var idx = 1
        while idx < args.count {
            switch args[idx] {
            case "--home" where idx + 1 < args.count:
                home = args[idx + 1]
                idx += 2
            case "--python" where idx + 1 < args.count:
                py = args[idx + 1]
                idx += 2
            case "--llr" where idx + 1 < args.count:
                llr = args[idx + 1]
                idx += 2
            default:
                idx += 1
            }
        }
        return RuntimeCLI(homePath: home, pythonBin: py, llrScript: llr)
    }

    func executeJSON(_ subargs: [String]) -> Data? {
        guard !llrScript.isEmpty else { return nil }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonBin)
        proc.arguments = [llrScript, "--home", homePath] + subargs
        let outPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = FileHandle.nullDevice
        do {
            try proc.run()
        } catch {
            return nil
        }
        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        proc.waitUntilExit()
        guard proc.terminationStatus == 0 || proc.terminationStatus == 2 else {
            return nil
        }
        return data
    }
}

// MARK: - Observable Store

@MainActor
final class DashboardViewModel: ObservableObject {
    @Published var snapshot: MenuBarSnapshot?
    @Published var selectedTab: Int = 0
    @Published var isBusyAction: Bool = false
    @Published var toastMessage: String = ""
    @Published var popoverVisible: Bool = false

    let cli: RuntimeCLI
    var onStatusBarChange: ((MenuBarSnapshot?) -> Void)?
    private var timer: Timer?
    private var fetchInFlight = false

    init(cli: RuntimeCLI) {
        self.cli = cli
    }

    func startPolling() {
        refreshNow()
        scheduleTimer(interval: 8.0)
    }

    func setPopoverVisible(_ visible: Bool) {
        popoverVisible = visible
        if visible {
            refreshNow()
            scheduleTimer(interval: 2.5)
        } else {
            scheduleTimer(interval: 8.0)
        }
    }

    private func scheduleTimer(interval: TimeInterval) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.refreshNow()
            }
        }
    }

    func refreshNow() {
        guard !fetchInFlight else { return }
        fetchInFlight = true
        let localCLI = cli
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let data = localCLI.executeJSON(["menubar-snapshot"])
            let decoded: MenuBarSnapshot?
            if let data = data {
                decoded = try? JSONDecoder().decode(MenuBarSnapshot.self, from: data)
            } else {
                decoded = nil
            }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.fetchInFlight = false
                if let decoded = decoded {
                    self.snapshot = decoded
                    self.onStatusBarChange?(decoded)
                }
            }
        }
    }

    func openPortal() {
        guard !isBusyAction else { return }
        isBusyAction = true
        toastMessage = "正在生成并打开本地知识门户..."
        let localCLI = cli
        let ws = snapshot?.primary_workspace
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var args = ["menubar-open-portal"]
            if let ws = ws, !ws.isEmpty {
                args += ["--workspace", ws]
            }
            _ = localCLI.executeJSON(args)
            DispatchQueue.main.async {
                self?.isBusyAction = false
                self?.showTemporaryToast("已在浏览器打开本地知识门户")
            }
        }
    }

    func runDreamPass() {
        guard !isBusyAction else { return }
        guard let ws = snapshot?.primary_workspace, !ws.isEmpty else {
            showTemporaryToast("暂无已注册工作区可整理")
            return
        }
        isBusyAction = true
        toastMessage = "正在巡检源码锚点与离线整理..."
        let localCLI = cli
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            _ = localCLI.executeJSON(["dream-pass", "--workspace", ws, "--apply", "--approve"])
            DispatchQueue.main.async {
                self?.isBusyAction = false
                self?.showTemporaryToast("已完成离线锚点巡检与整理")
                self?.refreshNow()
            }
        }
    }

    private func showTemporaryToast(_ text: String) {
        toastMessage = text
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.2) { [weak self] in
            if self?.toastMessage == text {
                self?.toastMessage = ""
            }
        }
    }
}

// MARK: - SwiftUI Popover Views

struct StatusPill: View {
    let dotColor: Color
    let label: String

    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(dotColor)
                .frame(width: 6, height: 6)
            Text(label)
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(.secondary)
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(Color.primary.opacity(0.06))
        .clipShape(Capsule())
    }
}

struct MetricCard: View {
    let title: String
    let value: String
    let subtitle: String
    let accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.system(size: 10, weight: .medium))
                .foregroundColor(.secondary)
            Text(value)
                .font(.system(size: 17, weight: .bold, design: .rounded))
                .foregroundColor(accent)
            Text(subtitle)
                .font(.system(size: 9))
                .foregroundColor(.secondary.opacity(0.85))
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 9)
        .padding(.vertical, 7)
        .background(Color.primary.opacity(0.045))
        .clipShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
    }
}

struct KnowledgeKindTag: View {
    let kind: String

    private var meta: (String, Color) {
        switch kind {
        case "memo":
            return ("Memo", Color.orange)
        case "solution":
            return ("契约", Color.purple)
        default:
            return ("Claim", Color.blue)
        }
    }

    var body: some View {
        Text(meta.0)
            .font(.system(size: 9, weight: .semibold))
            .foregroundColor(meta.1)
            .padding(.horizontal, 5)
            .padding(.vertical, 1.5)
            .background(meta.1.opacity(0.14))
            .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
    }
}

struct SessionCardView: View {
    let item: LiveSessionItem

    var isRunning: Bool { item.state == "active" }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                HStack(spacing: 4) {
                    Circle()
                        .fill(isRunning ? Color.green : Color.gray.opacity(0.55))
                        .frame(width: 7, height: 7)
                    Text(isRunning ? "执行中" : "待命")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(isRunning ? .green : .secondary)
                }
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background((isRunning ? Color.green : Color.primary).opacity(0.10))
                .clipShape(Capsule())

                Text("\(item.client) \(item.session_short)")
                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                    .foregroundColor(.secondary)

                Text(item.workspace)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary.opacity(0.85))
                    .lineLimit(1)

                Spacer()

                Text(item.updated_ago)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary.opacity(0.75))
            }

            Text(item.title)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.primary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)

            if !item.last_query.isEmpty {
                HStack(spacing: 4) {
                    Image(systemName: "magnifyingglass")
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                    Text(item.last_query)
                        .font(.system(size: 10))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
            }

            if !item.knowledge_used.isEmpty {
                Divider().opacity(0.45)
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 4) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 9))
                            .foregroundColor(.teal)
                        Text("本任务已调用本地知识 (\(item.knowledge_used.count))")
                            .font(.system(size: 10, weight: .semibold))
                            .foregroundColor(.teal)
                    }
                    ForEach(item.knowledge_used) { k in
                        HStack(spacing: 5) {
                            KnowledgeKindTag(kind: k.kind)
                            Text(k.title)
                                .font(.system(size: 10.5))
                                .foregroundColor(.primary.opacity(0.9))
                                .lineLimit(1)
                            Spacer(minLength: 4)
                            Text(k.time_ago)
                                .font(.system(size: 9.5))
                                .foregroundColor(.secondary.opacity(0.75))
                        }
                    }
                }
            } else {
                Text("本会话暂未触发本地知识检索")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary.opacity(0.65))
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color.primary.opacity(isRunning ? 0.065 : 0.038))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .strokeBorder(isRunning ? Color.green.opacity(0.38) : Color.primary.opacity(0.06), lineWidth: 1)
        )
    }
}

struct ActivityRowView: View {
    let item: ActivityFeedItem

    var isLearned: Bool { item.event_type == "learned" }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 6) {
                HStack(spacing: 3) {
                    Image(systemName: isLearned ? "checkmark.seal.fill" : "bolt.fill")
                        .font(.system(size: 9))
                    Text(item.action)
                        .font(.system(size: 10, weight: .semibold))
                }
                .foregroundColor(isLearned ? .green : .teal)

                KnowledgeKindTag(kind: item.kind)

                Text(item.workspace)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .lineLimit(1)

                Spacer()

                Text(item.time_ago)
                    .font(.system(size: 10))
                    .foregroundColor(.secondary.opacity(0.8))
            }

            Text(item.title)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(.primary)
                .lineLimit(2)

            if !item.task_title.isEmpty {
                HStack(spacing: 4) {
                    Text(item.session_short)
                        .font(.system(size: 9.5, weight: .medium, design: .monospaced))
                        .foregroundColor(.secondary)
                    Text("·")
                        .foregroundColor(.secondary)
                    Text(item.task_title)
                        .font(.system(size: 10.5))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
            }
        }
        .padding(9)
        .background(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(Color.primary.opacity(0.04))
        )
    }
}

struct DashboardPopoverView: View {
    @ObservedObject var vm: DashboardViewModel

    var body: some View {
        VStack(spacing: 10) {
            headerSection
            if let snap = vm.snapshot {
                metricsSection(snap)
                Picker("", selection: $vm.selectedTab) {
                    Text("当前会话任务 (\(snap.live_sessions.count))").tag(0)
                    Text("知识调用与学习 (\(snap.recent_activity.count))").tag(1)
                }
                .pickerStyle(.segmented)
                .labelsHidden()

                ScrollView {
                    LazyVStack(spacing: 8) {
                        if vm.selectedTab == 0 {
                            if snap.live_sessions.isEmpty {
                                emptyState(
                                    icon: "rectangle.stack.badge.person.crop",
                                    title: "当前没有活跃 IDE 会话心跳",
                                    subtitle: "打开 VS Code 或 Codex 对话后，这里会实时展示正在执行的任务与命中的本地知识。"
                                )
                            } else {
                                ForEach(snap.live_sessions) { sess in
                                    SessionCardView(item: sess)
                                }
                            }
                        } else {
                            if snap.recent_activity.isEmpty {
                                emptyState(
                                    icon: "sparkles.rectangle.stack",
                                    title: "尚无本地知识调用流水",
                                    subtitle: "当 Agent 命中本地 Memo / Claim 或完成验证沉淀时，这里会实时记录。"
                                )
                            } else {
                                ForEach(snap.recent_activity) { act in
                                    ActivityRowView(item: act)
                                }
                            }
                        }
                    }
                    .padding(.vertical, 2)
                }
            } else {
                Spacer()
                ProgressView("正在读取本地知识库...")
                    .font(.system(size: 12))
                Spacer()
            }

            footerSection
        }
        .padding(13)
        .frame(width: 410, height: 545)
    }

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 9) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(
                            LinearGradient(
                                colors: [Color.teal, Color.blue],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 30, height: 30)
                    Image(systemName: "brain.head.profile")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundColor(.white)
                }

                VStack(alignment: .leading, spacing: 1) {
                    HStack(spacing: 6) {
                        Text("Clone Yourself")
                            .font(.system(size: 13.5, weight: .bold))
                        if let ws = vm.snapshot?.primary_workspace {
                            Text(ws)
                                .font(.system(size: 10, weight: .medium))
                                .foregroundColor(.teal)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 1.5)
                                .background(Color.teal.opacity(0.12))
                                .clipShape(Capsule())
                        }
                    }
                    Text(vm.snapshot.map { "最近命中: \($0.last_hit_ago) · \($0.machine_name)" } ?? "本地经验运行时")
                        .font(.system(size: 10.5))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }

                Spacer()
            }

            if let host = vm.snapshot?.host {
                HStack(spacing: 6) {
                    StatusPill(
                        dotColor: host.online ? .green : .orange,
                        label: host.online ? "Host 守护在线" : "Host 空闲休眠"
                    )
                    if host.teams_away_only || host.teams_inbox {
                        StatusPill(dotColor: .blue, label: "Teams 远程联动")
                    }
                    if host.auto_dream {
                        StatusPill(dotColor: .purple, label: "空闲自动整理")
                    }
                    Spacer()
                }
            }
        }
    }

    private func metricsSection(_ snap: MenuBarSnapshot) -> some View {
        HStack(spacing: 6) {
            MetricCard(
                title: "24h 知识命中",
                value: "\(snap.totals.today_hits)",
                subtitle: "累计复用 \(snap.totals.total_hits) 次",
                accent: .teal
            )
            MetricCard(
                title: "避坑 Memo",
                value: "\(snap.totals.memos)",
                subtitle: "强约束红线",
                accent: .orange
            )
            MetricCard(
                title: "实证 Claim",
                value: "\(snap.totals.claims)",
                subtitle: "带锚点证据",
                accent: .blue
            )
            MetricCard(
                title: "零模型契约",
                value: "\(snap.totals.solutions)",
                subtitle: "P/R/Q 直出",
                accent: .purple
            )
        }
    }

    private func emptyState(icon: String, title: String, subtitle: String) -> some View {
        VStack(spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 26))
                .foregroundColor(.secondary.opacity(0.6))
            Text(title)
                .font(.system(size: 12, weight: .semibold))
            Text(subtitle)
                .font(.system(size: 11))
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 20)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 48)
    }

    private var footerSection: some View {
        VStack(spacing: 5) {
            if !vm.toastMessage.isEmpty {
                Text(vm.toastMessage)
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundColor(.teal)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider().opacity(0.5)
            HStack(spacing: 8) {
                Button(action: { vm.openPortal() }) {
                    Label("知识门户", systemImage: "safari")
                        .font(.system(size: 11, weight: .medium))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(vm.isBusyAction)

                Button(action: { vm.runDreamPass() }) {
                    Label("立即巡检整理", systemImage: "moon.stars")
                        .font(.system(size: 11, weight: .medium))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .disabled(vm.isBusyAction)

                Spacer()

                Button(action: { vm.refreshNow() }) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 11))
                }
                .buttonStyle(.borderless)
                .help("立即刷新状态")

                Button(action: { NSApplication.shared.terminate(nil) }) {
                    Text("退出图标")
                        .font(.system(size: 10.5))
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.borderless)
            }
        }
    }
}

// MARK: - AppKit Status Bar Controller

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSPopoverDelegate {
    private var statusItem: NSStatusItem!
    private let popover = NSPopover()
    private var viewModel: DashboardViewModel!

    func applicationDidFinishLaunching(_ notification: Notification) {
        let cli = RuntimeCLI.parseFromCommandLine()
        viewModel = DashboardViewModel(cli: cli)

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            let symbolCfg = NSImage.SymbolConfiguration(pointSize: 13.5, weight: .medium)
            let img = NSImage(
                systemSymbolName: "brain.head.profile",
                accessibilityDescription: "Clone Yourself 本地知识状态"
            )?.withSymbolConfiguration(symbolCfg)
            img?.isTemplate = true
            button.image = img
            button.imagePosition = .imageLeading
            button.font = NSFont.monospacedDigitSystemFont(ofSize: 11.5, weight: .medium)
            button.target = self
            button.action = #selector(togglePopover(_:))
            button.toolTip = "Clone Yourself · 本地知识实时状态"
        }

        popover.contentSize = NSSize(width: 410, height: 545)
        popover.behavior = .transient
        popover.animates = true
        popover.delegate = self
        popover.contentViewController = NSHostingController(rootView: DashboardPopoverView(vm: viewModel))

        viewModel.onStatusBarChange = { [weak self] snap in
            self?.updateStatusButton(snap)
        }
        viewModel.startPolling()
    }

    private func updateStatusButton(_ snap: MenuBarSnapshot?) {
        guard let button = statusItem.button, let snap = snap else { return }
        let hits = snap.totals.today_hits
        let active = snap.totals.active_sessions
        if active > 0 {
            button.title = hits > 0 ? " ⚡\(hits)" : " ⚡"
        } else if snap.just_hit_knowledge {
            button.title = " ✨\(hits)"
        } else if hits > 0 {
            button.title = " \(hits)"
        } else {
            button.title = ""
        }
        button.toolTip = "Clone Yourself · 24h 命中本地知识 \(hits) 次 · \(active) 个会话执行中 · 最近命中: \(snap.last_hit_ago)"
    }

    @objc private func togglePopover(_ sender: AnyObject?) {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            popover.performClose(sender)
            viewModel.setPopoverVisible(false)
        } else {
            NSApplication.shared.activate(ignoringOtherApps: true)
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
            viewModel.setPopoverVisible(true)
        }
    }

    func popoverDidClose(_ notification: Notification) {
        viewModel.setPopoverVisible(false)
    }
}

// MARK: - Single-Instance Lock & Entrypoint

private var globalLockFD: Int32 = -1

private func acquireSingleInstanceLock(homePath: String) -> Bool {
    let fm = FileManager.default
    try? fm.createDirectory(atPath: homePath, withIntermediateDirectories: true)
    let lockPath = (homePath as NSString).appendingPathComponent("menubar.lock")
    let fd = open(lockPath, O_CREAT | O_RDWR, S_IRUSR | S_IWUSR)
    guard fd >= 0 else { return false }
    if flock(fd, LOCK_EX | LOCK_NB) != 0 {
        close(fd)
        return false
    }
    globalLockFD = fd
    let pidPath = (homePath as NSString).appendingPathComponent("menubar.pid")
    let pidText = "\(getpid())\n"
    try? pidText.write(toFile: pidPath, atomically: true, encoding: .utf8)
    return true
}

@main
struct CloneYourselfMenuBarMain {
    static func main() {
        let cli = RuntimeCLI.parseFromCommandLine()
        guard acquireSingleInstanceLock(homePath: cli.homePath) else {
            // Another instance already owns the menu bar icon; exit quietly.
            exit(0)
        }
        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)
        let delegate = AppDelegate()
        app.delegate = delegate
        app.run()
    }
}
