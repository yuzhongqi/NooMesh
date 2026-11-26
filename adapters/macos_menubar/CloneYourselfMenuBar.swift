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

// MARK: - Apple HIG Design Tokens & Surfaces

enum HIGPalette {
    static let primaryLabel = Color(nsColor: .labelColor)
    static let secondaryLabel = Color(nsColor: .secondaryLabelColor)
    static let tertiaryLabel = Color(nsColor: .tertiaryLabelColor)
    static let onlineDot = Color(nsColor: .systemGreen)
    static let standbyDot = Color(nsColor: .tertiaryLabelColor)

    static func platterFill(_ scheme: ColorScheme, emphasized: Bool = false) -> Color {
        if scheme == .dark {
            return Color.white.opacity(emphasized ? 0.11 : 0.075)
        } else {
            return Color.white.opacity(emphasized ? 0.86 : 0.68)
        }
    }

    static func platterStroke(_ scheme: ColorScheme, active: Bool = false) -> Color {
        if active {
            return scheme == .dark ? Color.white.opacity(0.22) : Color.black.opacity(0.14)
        }
        return scheme == .dark ? Color.white.opacity(0.09) : Color.black.opacity(0.06)
    }

    static func recessedFill(_ scheme: ColorScheme) -> Color {
        return scheme == .dark ? Color.black.opacity(0.24) : Color.black.opacity(0.055)
    }

    static func chipFill(_ scheme: ColorScheme) -> Color {
        return scheme == .dark ? Color.white.opacity(0.09) : Color.black.opacity(0.055)
    }
}

struct ApplePlatterModifier: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme
    var cornerRadius: CGFloat = 11
    var emphasized: Bool = false
    var activeBorder: Bool = false

    func body(content: Content) -> some View {
        content
            .background(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(HIGPalette.platterFill(colorScheme, emphasized: emphasized))
                    .shadow(
                        color: colorScheme == .dark ? .clear : Color.black.opacity(0.035),
                        radius: 2, x: 0, y: 1
                    )
            )
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(HIGPalette.platterStroke(colorScheme, active: activeBorder), lineWidth: 0.5)
            )
    }
}

extension View {
    func applePlatter(cornerRadius: CGFloat = 11, emphasized: Bool = false, activeBorder: Bool = false) -> some View {
        modifier(ApplePlatterModifier(cornerRadius: cornerRadius, emphasized: emphasized, activeBorder: activeBorder))
    }
}

// MARK: - Native Control-Center Style Views

struct ServiceStatusItem: View {
    let symbol: String?
    let dotColor: Color?
    let label: String

    var body: some View {
        HStack(spacing: 4.5) {
            if let dot = dotColor {
                Circle()
                    .fill(dot)
                    .frame(width: 6, height: 6)
            } else if let sym = symbol {
                Image(systemName: sym)
                    .font(.system(size: 9.5, weight: .medium))
                    .foregroundColor(HIGPalette.secondaryLabel)
            }
            Text(label)
                .font(.system(size: 10.5, weight: .regular))
                .foregroundColor(HIGPalette.secondaryLabel)
        }
    }
}

struct MetricCard: View {
    let title: String
    let value: String
    let subtitle: String

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.system(size: 10.5, weight: .medium))
                .foregroundColor(HIGPalette.secondaryLabel)
                .lineLimit(1)
            Text(value)
                .font(.system(size: 18, weight: .semibold, design: .rounded).monospacedDigit())
                .foregroundColor(HIGPalette.primaryLabel)
            Text(subtitle)
                .font(.system(size: 9.5))
                .foregroundColor(HIGPalette.tertiaryLabel)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .applePlatter(cornerRadius: 10)
    }
}

struct ControlCenterSegmentedBar: View {
    @Environment(\.colorScheme) private var colorScheme
    @Binding var selection: Int
    let leftTitle: String
    let rightTitle: String

    var body: some View {
        HStack(spacing: 3) {
            segmentButton(index: 0, title: leftTitle)
            segmentButton(index: 1, title: rightTitle)
        }
        .padding(3)
        .background(
            RoundedRectangle(cornerRadius: 8.5, style: .continuous)
                .fill(HIGPalette.recessedFill(colorScheme))
        )
    }

    private func segmentButton(index: Int, title: String) -> some View {
        let isSelected = selection == index
        return Button(action: {
            withAnimation(.easeInOut(duration: 0.14)) {
                selection = index
            }
        }) {
            Text(title)
                .font(.system(size: 11.5, weight: isSelected ? .semibold : .regular))
                .foregroundColor(isSelected ? HIGPalette.primaryLabel : HIGPalette.secondaryLabel)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 4.5)
                .background(
                    Group {
                        if isSelected {
                            RoundedRectangle(cornerRadius: 6.5, style: .continuous)
                                .fill(colorScheme == .dark ? Color.white.opacity(0.15) : Color.white.opacity(0.95))
                                .shadow(
                                    color: Color.black.opacity(colorScheme == .dark ? 0.22 : 0.07),
                                    radius: 1.5, x: 0, y: 0.5
                                )
                        }
                    }
                )
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

struct KnowledgeKindTag: View {
    @Environment(\.colorScheme) private var colorScheme
    let kind: String

    private var meta: (String, String) {
        switch kind {
        case "memo":
            return ("shield.lefthalf.filled", "Memo")
        case "solution":
            return ("bolt.horizontal.fill", "契约")
        default:
            return ("checkmark.seal", "Claim")
        }
    }

    var body: some View {
        HStack(spacing: 3) {
            Image(systemName: meta.0)
                .font(.system(size: 8.5, weight: .medium))
            Text(meta.1)
                .font(.system(size: 9.5, weight: .medium))
        }
        .foregroundColor(HIGPalette.secondaryLabel)
        .padding(.horizontal, 5.5)
        .padding(.vertical, 2)
        .background(
            RoundedRectangle(cornerRadius: 4.5, style: .continuous)
                .fill(HIGPalette.chipFill(colorScheme))
        )
    }
}

struct SessionCardView: View {
    @Environment(\.colorScheme) private var colorScheme
    let item: LiveSessionItem

    var isRunning: Bool { item.state == "active" }

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 6) {
                HStack(spacing: 4.5) {
                    Circle()
                        .fill(isRunning ? HIGPalette.onlineDot : HIGPalette.standbyDot)
                        .frame(width: 6, height: 6)
                    Text(isRunning ? "执行中" : "待命")
                        .font(.system(size: 10.5, weight: isRunning ? .semibold : .medium))
                        .foregroundColor(isRunning ? HIGPalette.primaryLabel : HIGPalette.secondaryLabel)
                }

                Text("·")
                    .foregroundColor(HIGPalette.tertiaryLabel)

                Text("\(item.client) \(item.session_short)")
                    .font(.system(size: 10.5, weight: .regular, design: .monospaced))
                    .foregroundColor(HIGPalette.secondaryLabel)

                Text(item.workspace)
                    .font(.system(size: 10.5))
                    .foregroundColor(HIGPalette.tertiaryLabel)
                    .lineLimit(1)

                Spacer()

                Text(item.updated_ago)
                    .font(.system(size: 10))
                    .foregroundColor(HIGPalette.tertiaryLabel)
            }

            Text(item.title)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(HIGPalette.primaryLabel)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)

            if !item.last_query.isEmpty {
                HStack(spacing: 4.5) {
                    Image(systemName: "magnifyingglass")
                        .font(.system(size: 9.5))
                        .foregroundColor(HIGPalette.tertiaryLabel)
                    Text(item.last_query)
                        .font(.system(size: 10.5))
                        .foregroundColor(HIGPalette.secondaryLabel)
                        .lineLimit(1)
                }
            }

            if !item.knowledge_used.isEmpty {
                Divider()
                    .opacity(colorScheme == .dark ? 0.35 : 0.5)
                VStack(alignment: .leading, spacing: 4.5) {
                    HStack(spacing: 4.5) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 9.5, weight: .medium))
                            .foregroundColor(HIGPalette.secondaryLabel)
                        Text("已调用本地知识 (\(item.knowledge_used.count))")
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundColor(HIGPalette.secondaryLabel)
                    }
                    ForEach(item.knowledge_used) { k in
                        HStack(spacing: 6) {
                            KnowledgeKindTag(kind: k.kind)
                            Text(k.title)
                                .font(.system(size: 11))
                                .foregroundColor(HIGPalette.primaryLabel)
                                .lineLimit(1)
                            Spacer(minLength: 4)
                            Text(k.time_ago)
                                .font(.system(size: 10))
                                .foregroundColor(HIGPalette.tertiaryLabel)
                        }
                    }
                }
            } else {
                Text("本会话暂未触发本地知识检索")
                    .font(.system(size: 10.5))
                    .foregroundColor(HIGPalette.tertiaryLabel)
            }
        }
        .padding(11)
        .applePlatter(cornerRadius: 11, emphasized: isRunning, activeBorder: isRunning)
    }
}

struct ActivityRowView: View {
    let item: ActivityFeedItem

    var isLearned: Bool { item.event_type == "learned" }

    var body: some View {
        VStack(alignment: .leading, spacing: 5.5) {
            HStack(spacing: 6) {
                HStack(spacing: 3.5) {
                    Image(systemName: isLearned ? "checkmark.circle.fill" : "arrow.turn.down.right")
                        .font(.system(size: 9.5, weight: .medium))
                    Text(item.action)
                        .font(.system(size: 10.5, weight: .medium))
                }
                .foregroundColor(HIGPalette.secondaryLabel)

                KnowledgeKindTag(kind: item.kind)

                Text(item.workspace)
                    .font(.system(size: 10.5))
                    .foregroundColor(HIGPalette.tertiaryLabel)
                    .lineLimit(1)

                Spacer()

                Text(item.time_ago)
                    .font(.system(size: 10))
                    .foregroundColor(HIGPalette.tertiaryLabel)
            }

            Text(item.title)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(HIGPalette.primaryLabel)
                .lineLimit(2)

            if !item.task_title.isEmpty {
                HStack(spacing: 4) {
                    Text(item.session_short)
                        .font(.system(size: 10, weight: .regular, design: .monospaced))
                        .foregroundColor(HIGPalette.tertiaryLabel)
                    Text("·")
                        .foregroundColor(HIGPalette.tertiaryLabel)
                    Text(item.task_title)
                        .font(.system(size: 10.5))
                        .foregroundColor(HIGPalette.secondaryLabel)
                        .lineLimit(1)
                }
            }
        }
        .padding(10.5)
        .applePlatter(cornerRadius: 10)
    }
}

struct FooterActionPill: View {
    let title: String
    let systemImage: String
    let disabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4.5) {
                Image(systemName: systemImage)
                    .font(.system(size: 10.5, weight: .medium))
                Text(title)
                    .font(.system(size: 11, weight: .medium))
            }
            .foregroundColor(HIGPalette.primaryLabel)
            .padding(.horizontal, 9.5)
            .padding(.vertical, 5)
            .applePlatter(cornerRadius: 7.5)
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.5 : 1.0)
    }
}

struct DashboardPopoverView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject var vm: DashboardViewModel

    var body: some View {
        VStack(spacing: 11) {
            headerSection
            if let snap = vm.snapshot {
                metricsSection(snap)
                ControlCenterSegmentedBar(
                    selection: $vm.selectedTab,
                    leftTitle: "当前会话任务 (\(snap.live_sessions.count))",
                    rightTitle: "知识调用与学习 (\(snap.recent_activity.count))"
                )

                ScrollView(.vertical, showsIndicators: false) {
                    VStack(spacing: 8) {
                        if vm.selectedTab == 0 {
                            if snap.live_sessions.isEmpty {
                                emptyState(
                                    icon: "rectangle.stack",
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
                    .padding(.vertical, 1)
                }
            } else {
                Spacer()
                ProgressView("正在读取本地知识库…")
                    .font(.system(size: 12))
                Spacer()
            }

            footerSection
        }
        .padding(14)
        .frame(width: 410, height: 545)
    }

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                ZStack {
                    Circle()
                        .fill(HIGPalette.chipFill(colorScheme))
                        .frame(width: 32, height: 32)
                    Image(systemName: "brain.head.profile")
                        .font(.system(size: 15, weight: .medium))
                        .foregroundColor(HIGPalette.primaryLabel)
                }

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text("Clone Yourself")
                            .font(.system(size: 13.5, weight: .semibold))
                            .foregroundColor(HIGPalette.primaryLabel)
                        if let ws = vm.snapshot?.primary_workspace {
                            Text(ws)
                                .font(.system(size: 10, weight: .medium))
                                .foregroundColor(HIGPalette.secondaryLabel)
                                .padding(.horizontal, 6.5)
                                .padding(.vertical, 1.5)
                                .background(
                                    Capsule()
                                        .fill(HIGPalette.chipFill(colorScheme))
                                )
                        }
                    }
                    Text(vm.snapshot.map { "最近命中 \($0.last_hit_ago) · \($0.machine_name)" } ?? "本地经验运行时")
                        .font(.system(size: 10.5))
                        .foregroundColor(HIGPalette.secondaryLabel)
                        .lineLimit(1)
                }

                Spacer()
            }

            if let host = vm.snapshot?.host {
                HStack(spacing: 12) {
                    ServiceStatusItem(
                        symbol: nil,
                        dotColor: host.online ? HIGPalette.onlineDot : HIGPalette.standbyDot,
                        label: host.online ? "Host 在线" : "Host 按需休眠"
                    )
                    if host.teams_away_only || host.teams_inbox {
                        ServiceStatusItem(
                            symbol: "iphone",
                            dotColor: nil,
                            label: "Teams 远程接力"
                        )
                    }
                    if host.auto_dream {
                        ServiceStatusItem(
                            symbol: "moon",
                            dotColor: nil,
                            label: "空闲自动整理"
                        )
                    }
                    Spacer()
                }
                .padding(.leading, 2)
            }
        }
    }

    private func metricsSection(_ snap: MenuBarSnapshot) -> some View {
        HStack(spacing: 7) {
            MetricCard(
                title: "24h 知识命中",
                value: "\(snap.totals.today_hits)",
                subtitle: "累计 \(snap.totals.total_hits) 次"
            )
            MetricCard(
                title: "避坑 Memo",
                value: "\(snap.totals.memos)",
                subtitle: "强约束规则"
            )
            MetricCard(
                title: "实证 Claim",
                value: "\(snap.totals.claims)",
                subtitle: "带锚点证据"
            )
            MetricCard(
                title: "零模型契约",
                value: "\(snap.totals.solutions)",
                subtitle: "P/R/Q 直出"
            )
        }
    }

    private func emptyState(icon: String, title: String, subtitle: String) -> some View {
        VStack(spacing: 8) {
            Image(systemName: icon)
                .font(.system(size: 24, weight: .light))
                .foregroundColor(HIGPalette.tertiaryLabel)
            Text(title)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(HIGPalette.primaryLabel)
            Text(subtitle)
                .font(.system(size: 11))
                .foregroundColor(HIGPalette.secondaryLabel)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 24)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 48)
    }

    private var footerSection: some View {
        VStack(spacing: 7) {
            if !vm.toastMessage.isEmpty {
                Text(vm.toastMessage)
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundColor(HIGPalette.secondaryLabel)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Divider()
                .opacity(colorScheme == .dark ? 0.35 : 0.5)
            HStack(spacing: 7) {
                FooterActionPill(
                    title: "知识门户",
                    systemImage: "safari",
                    disabled: vm.isBusyAction,
                    action: { vm.openPortal() }
                )
                FooterActionPill(
                    title: "巡检整理",
                    systemImage: "arrow.triangle.2.circlepath",
                    disabled: vm.isBusyAction,
                    action: { vm.runDreamPass() }
                )

                Spacer()

                Button(action: { vm.refreshNow() }) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(HIGPalette.secondaryLabel)
                        .frame(width: 24, height: 24)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("刷新状态")

                Button(action: { NSApplication.shared.terminate(nil) }) {
                    Text("退出")
                        .font(.system(size: 11))
                        .foregroundColor(HIGPalette.secondaryLabel)
                        .padding(.horizontal, 4)
                }
                .buttonStyle(.plain)
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
            button.title = hits > 0 ? " \(hits)·" : " ·"
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
