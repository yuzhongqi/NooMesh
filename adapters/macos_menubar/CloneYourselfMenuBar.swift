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

// MARK: - Liquid Glass Optical Tokens & Modifiers

enum GlassPalette {
    static let primaryLabel = Color(nsColor: .labelColor)
    static let secondaryLabel = Color(nsColor: .secondaryLabelColor)
    static let tertiaryLabel = Color(nsColor: .tertiaryLabelColor)
    static let onlineDot = Color(nsColor: .systemGreen)
    static let standbyDot = Color(nsColor: .tertiaryLabelColor)

    static func bodyGradient(_ scheme: ColorScheme, emphasized: Bool = false) -> LinearGradient {
        if scheme == .dark {
            return LinearGradient(
                colors: [
                    Color.white.opacity(emphasized ? 0.16 : 0.105),
                    Color.white.opacity(emphasized ? 0.075 : 0.042)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        } else {
            return LinearGradient(
                colors: [
                    Color.white.opacity(emphasized ? 0.68 : 0.50),
                    Color.white.opacity(emphasized ? 0.42 : 0.26)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
    }

    static func specularRimGradient(_ scheme: ColorScheme, active: Bool = false) -> LinearGradient {
        if scheme == .dark {
            return LinearGradient(
                stops: [
                    .init(color: Color.white.opacity(active ? 0.42 : 0.26), location: 0.0),
                    .init(color: Color.white.opacity(0.04), location: 0.48),
                    .init(color: Color.white.opacity(active ? 0.22 : 0.12), location: 1.0)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        } else {
            return LinearGradient(
                stops: [
                    .init(color: Color.white.opacity(active ? 0.95 : 0.82), location: 0.0),
                    .init(color: Color.white.opacity(0.20), location: 0.48),
                    .init(color: Color.white.opacity(active ? 0.62 : 0.46), location: 1.0)
                ],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
    }

    static func recessedChannelFill(_ scheme: ColorScheme) -> Color {
        return scheme == .dark ? Color.black.opacity(0.26) : Color.black.opacity(0.055)
    }

    static func microCapsuleFill(_ scheme: ColorScheme) -> Color {
        return scheme == .dark ? Color.white.opacity(0.10) : Color.white.opacity(0.52)
    }
}

struct LiquidDropletModifier: ViewModifier {
    @Environment(\.colorScheme) private var colorScheme
    @State private var hoverLocation: CGPoint? = nil

    var cornerRadius: CGFloat = 15
    var emphasized: Bool = false
    var activeRim: Bool = false
    var interactiveHover: Bool = true

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        content
            .background(
                ZStack {
                    // 1. Translucent refractive droplet body
                    shape.fill(GlassPalette.bodyGradient(colorScheme, emphasized: emphasized))

                    // 2. Top-edge convex liquid meniscus sheen
                    LinearGradient(
                        stops: [
                            .init(color: Color.white.opacity(colorScheme == .dark ? 0.16 : 0.55), location: 0.0),
                            .init(color: Color.clear, location: 0.42)
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                    .clipShape(shape)

                    // 3. Interactive cursor-tracking liquid caustic highlight
                    if let pt = hoverLocation {
                        RadialGradient(
                            colors: [
                                Color.white.opacity(colorScheme == .dark ? 0.16 : 0.44),
                                Color.clear
                            ],
                            center: .topLeading,
                            startRadius: 2,
                            endRadius: 120
                        )
                        .offset(x: pt.x - 16, y: pt.y - 16)
                        .clipShape(shape)
                    }
                }
                .shadow(
                    color: Color.black.opacity(colorScheme == .dark ? 0.24 : 0.065),
                    radius: emphasized ? 8 : 5,
                    x: 0,
                    y: emphasized ? 4 : 2
                )
            )
            .overlay(
                // 4. Diagonal dual-catchlight optical glass rim
                shape.strokeBorder(
                    GlassPalette.specularRimGradient(colorScheme, active: activeRim),
                    lineWidth: activeRim ? 1.0 : 0.8
                )
            )
            .onContinuousHover { phase in
                guard interactiveHover else { return }
                switch phase {
                case .active(let loc):
                    hoverLocation = loc
                case .ended:
                    hoverLocation = nil
                }
            }
    }
}

extension View {
    func liquidDroplet(
        cornerRadius: CGFloat = 15,
        emphasized: Bool = false,
        activeRim: Bool = false,
        interactiveHover: Bool = true
    ) -> some View {
        modifier(
            LiquidDropletModifier(
                cornerRadius: cornerRadius,
                emphasized: emphasized,
                activeRim: activeRim,
                interactiveHover: interactiveHover
            )
        )
    }
}

// MARK: - Liquid Glass Views

struct ServiceStatusItem: View {
    @Environment(\.colorScheme) private var colorScheme
    let symbol: String?
    let dotColor: Color?
    let label: String

    var body: some View {
        HStack(spacing: 4.5) {
            if let dot = dotColor {
                Circle()
                    .fill(dot)
                    .frame(width: 6, height: 6)
                    .shadow(color: dot.opacity(0.55), radius: 3, x: 0, y: 0)
            } else if let sym = symbol {
                Image(systemName: sym)
                    .font(.system(size: 9.5, weight: .medium))
                    .foregroundColor(GlassPalette.secondaryLabel)
            }
            Text(label)
                .font(.system(size: 10.5, weight: .regular))
                .foregroundColor(GlassPalette.secondaryLabel)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 3.5)
        .background(
            Capsule()
                .fill(GlassPalette.microCapsuleFill(colorScheme))
        )
        .overlay(
            Capsule()
                .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.65)
        )
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
                .foregroundColor(GlassPalette.secondaryLabel)
                .lineLimit(1)
            Text(value)
                .font(.system(size: 18.5, weight: .semibold, design: .rounded).monospacedDigit())
                .foregroundColor(GlassPalette.primaryLabel)
            Text(subtitle)
                .font(.system(size: 9.5))
                .foregroundColor(GlassPalette.tertiaryLabel)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 10)
        .padding(.vertical, 8.5)
        .liquidDroplet(cornerRadius: 14)
    }
}

struct LiquidSegmentedBar: View {
    @Environment(\.colorScheme) private var colorScheme
    @Namespace private var tabNamespace
    @Binding var selection: Int
    let leftTitle: String
    let rightTitle: String

    var body: some View {
        HStack(spacing: 4) {
            segmentButton(index: 0, title: leftTitle)
            segmentButton(index: 1, title: rightTitle)
        }
        .padding(3.5)
        .background(
            Capsule()
                .fill(GlassPalette.recessedChannelFill(colorScheme))
        )
        .overlay(
            Capsule()
                .strokeBorder(
                    Color.white.opacity(colorScheme == .dark ? 0.10 : 0.42),
                    lineWidth: 0.6
                )
        )
    }

    private func segmentButton(index: Int, title: String) -> some View {
        let isSelected = selection == index
        return Button(action: {
            withAnimation(.spring(response: 0.34, dampingFraction: 0.76)) {
                selection = index
            }
        }) {
            Text(title)
                .font(.system(size: 11.5, weight: isSelected ? .semibold : .regular))
                .foregroundColor(isSelected ? GlassPalette.primaryLabel : GlassPalette.secondaryLabel)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 5.5)
                .background(
                    ZStack {
                        if isSelected {
                            Capsule()
                                .fill(
                                    LinearGradient(
                                        colors: colorScheme == .dark
                                            ? [Color.white.opacity(0.22), Color.white.opacity(0.11)]
                                            : [Color.white.opacity(0.88), Color.white.opacity(0.62)],
                                        startPoint: .top,
                                        endPoint: .bottom
                                    )
                                )
                                .overlay(
                                    Capsule()
                                        .strokeBorder(
                                            GlassPalette.specularRimGradient(colorScheme, active: true),
                                            lineWidth: 0.85
                                        )
                                )
                                .shadow(
                                    color: Color.black.opacity(colorScheme == .dark ? 0.28 : 0.08),
                                    radius: 4, x: 0, y: 1.5
                                )
                                .matchedGeometryEffect(id: "activeLiquidPill", in: tabNamespace)
                        }
                    }
                )
                .contentShape(Capsule())
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
        .foregroundColor(GlassPalette.secondaryLabel)
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
        .background(
            Capsule()
                .fill(GlassPalette.microCapsuleFill(colorScheme))
        )
        .overlay(
            Capsule()
                .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.55)
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
                        .fill(isRunning ? GlassPalette.onlineDot : GlassPalette.standbyDot)
                        .frame(width: 6, height: 6)
                        .shadow(color: isRunning ? GlassPalette.onlineDot.opacity(0.6) : .clear, radius: 3)
                    Text(isRunning ? "执行中" : "待命")
                        .font(.system(size: 10.5, weight: isRunning ? .semibold : .medium))
                        .foregroundColor(isRunning ? GlassPalette.primaryLabel : GlassPalette.secondaryLabel)
                }

                Text("·")
                    .foregroundColor(GlassPalette.tertiaryLabel)

                Text("\(item.client) \(item.session_short)")
                    .font(.system(size: 10.5, weight: .regular, design: .monospaced))
                    .foregroundColor(GlassPalette.secondaryLabel)

                Text(item.workspace)
                    .font(.system(size: 10.5))
                    .foregroundColor(GlassPalette.tertiaryLabel)
                    .lineLimit(1)

                Spacer()

                Text(item.updated_ago)
                    .font(.system(size: 10))
                    .foregroundColor(GlassPalette.tertiaryLabel)
            }

            Text(item.title)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(GlassPalette.primaryLabel)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)

            if !item.last_query.isEmpty {
                HStack(spacing: 4.5) {
                    Image(systemName: "magnifyingglass")
                        .font(.system(size: 9.5))
                        .foregroundColor(GlassPalette.tertiaryLabel)
                    Text(item.last_query)
                        .font(.system(size: 10.5))
                        .foregroundColor(GlassPalette.secondaryLabel)
                        .lineLimit(1)
                }
            }

            if !item.knowledge_used.isEmpty {
                Rectangle()
                    .fill(Color.white.opacity(colorScheme == .dark ? 0.10 : 0.45))
                    .frame(height: 0.6)
                VStack(alignment: .leading, spacing: 4.5) {
                    HStack(spacing: 4.5) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 9.5, weight: .medium))
                            .foregroundColor(GlassPalette.secondaryLabel)
                        Text("已调用本地知识 (\(item.knowledge_used.count))")
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundColor(GlassPalette.secondaryLabel)
                    }
                    ForEach(item.knowledge_used) { k in
                        HStack(spacing: 6) {
                            KnowledgeKindTag(kind: k.kind)
                            Text(k.title)
                                .font(.system(size: 11))
                                .foregroundColor(GlassPalette.primaryLabel)
                                .lineLimit(1)
                            Spacer(minLength: 4)
                            Text(k.time_ago)
                                .font(.system(size: 10))
                                .foregroundColor(GlassPalette.tertiaryLabel)
                        }
                    }
                }
            } else {
                Text("本会话暂未触发本地知识检索")
                    .font(.system(size: 10.5))
                    .foregroundColor(GlassPalette.tertiaryLabel)
            }
        }
        .padding(11.5)
        .liquidDroplet(cornerRadius: 16, emphasized: isRunning, activeRim: isRunning)
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
                .foregroundColor(GlassPalette.secondaryLabel)

                KnowledgeKindTag(kind: item.kind)

                Text(item.workspace)
                    .font(.system(size: 10.5))
                    .foregroundColor(GlassPalette.tertiaryLabel)
                    .lineLimit(1)

                Spacer()

                Text(item.time_ago)
                    .font(.system(size: 10))
                    .foregroundColor(GlassPalette.tertiaryLabel)
            }

            Text(item.title)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(GlassPalette.primaryLabel)
                .lineLimit(2)

            if !item.task_title.isEmpty {
                HStack(spacing: 4) {
                    Text(item.session_short)
                        .font(.system(size: 10, weight: .regular, design: .monospaced))
                        .foregroundColor(GlassPalette.tertiaryLabel)
                    Text("·")
                        .foregroundColor(GlassPalette.tertiaryLabel)
                    Text(item.task_title)
                        .font(.system(size: 10.5))
                        .foregroundColor(GlassPalette.secondaryLabel)
                        .lineLimit(1)
                }
            }
        }
        .padding(11)
        .liquidDroplet(cornerRadius: 15)
    }
}

struct LiquidCapsuleButton: View {
    @Environment(\.colorScheme) private var colorScheme
    @State private var isHovering = false
    let title: String
    let systemImage: String
    let disabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 5) {
                Image(systemName: systemImage)
                    .font(.system(size: 10.5, weight: .medium))
                Text(title)
                    .font(.system(size: 11, weight: .medium))
            }
            .foregroundColor(GlassPalette.primaryLabel)
            .padding(.horizontal, 11.5)
            .padding(.vertical, 6)
            .background(
                Capsule()
                    .fill(GlassPalette.bodyGradient(colorScheme, emphasized: isHovering))
                    .shadow(
                        color: Color.black.opacity(colorScheme == .dark ? 0.24 : 0.06),
                        radius: isHovering ? 6 : 3, x: 0, y: 1.5
                    )
            )
            .overlay(
                Capsule()
                    .strokeBorder(
                        GlassPalette.specularRimGradient(colorScheme, active: isHovering),
                        lineWidth: 0.8
                    )
            )
            .scaleEffect(isHovering ? 1.02 : 1.0)
            .animation(.easeOut(duration: 0.14), value: isHovering)
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.5 : 1.0)
        .onHover { hovering in
            isHovering = hovering
        }
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
                LiquidSegmentedBar(
                    selection: $vm.selectedTab,
                    leftTitle: "当前会话任务 (\(snap.live_sessions.count))",
                    rightTitle: "知识调用与学习 (\(snap.recent_activity.count))"
                )

                ScrollView(.vertical, showsIndicators: false) {
                    VStack(spacing: 8.5) {
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
                    .padding(.vertical, 2)
                }
            } else {
                Spacer()
                ProgressView("正在读取本地知识库…")
                    .font(.system(size: 12))
                Spacer()
            }

            footerSection
        }
        .padding(15)
        .frame(width: 420, height: 556)
        .background(Color.clear)
    }

    private var headerSection: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 10.5) {
                ZStack {
                    Circle()
                        .fill(GlassPalette.bodyGradient(colorScheme, emphasized: true))
                        .frame(width: 34, height: 34)
                        .overlay(
                            Circle()
                                .strokeBorder(
                                    GlassPalette.specularRimGradient(colorScheme, active: true),
                                    lineWidth: 0.85
                                )
                        )
                        .shadow(
                            color: Color.black.opacity(colorScheme == .dark ? 0.25 : 0.07),
                            radius: 4, x: 0, y: 2
                        )
                    Image(systemName: "brain.head.profile")
                        .font(.system(size: 15.5, weight: .medium))
                        .foregroundColor(GlassPalette.primaryLabel)
                }

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text("Clone Yourself")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(GlassPalette.primaryLabel)
                        if let ws = vm.snapshot?.primary_workspace {
                            Text(ws)
                                .font(.system(size: 10, weight: .medium))
                                .foregroundColor(GlassPalette.secondaryLabel)
                                .padding(.horizontal, 7)
                                .padding(.vertical, 2)
                                .background(
                                    Capsule()
                                        .fill(GlassPalette.microCapsuleFill(colorScheme))
                                )
                                .overlay(
                                    Capsule()
                                        .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.55)
                                )
                        }
                    }
                    Text(vm.snapshot.map { "最近命中 \($0.last_hit_ago) · \($0.machine_name)" } ?? "本地经验运行时")
                        .font(.system(size: 10.5))
                        .foregroundColor(GlassPalette.secondaryLabel)
                        .lineLimit(1)
                }

                Spacer()
            }

            if let host = vm.snapshot?.host {
                HStack(spacing: 6) {
                    ServiceStatusItem(
                        symbol: nil,
                        dotColor: host.online ? GlassPalette.onlineDot : GlassPalette.standbyDot,
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
            }
        }
    }

    private func metricsSection(_ snap: MenuBarSnapshot) -> some View {
        HStack(spacing: 7.5) {
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
                .foregroundColor(GlassPalette.tertiaryLabel)
            Text(title)
                .font(.system(size: 12, weight: .medium))
                .foregroundColor(GlassPalette.primaryLabel)
            Text(subtitle)
                .font(.system(size: 11))
                .foregroundColor(GlassPalette.secondaryLabel)
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
                    .foregroundColor(GlassPalette.secondaryLabel)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            Rectangle()
                .fill(Color.white.opacity(colorScheme == .dark ? 0.12 : 0.45))
                .frame(height: 0.6)
            HStack(spacing: 7.5) {
                LiquidCapsuleButton(
                    title: "知识门户",
                    systemImage: "safari",
                    disabled: vm.isBusyAction,
                    action: { vm.openPortal() }
                )
                LiquidCapsuleButton(
                    title: "巡检整理",
                    systemImage: "arrow.triangle.2.circlepath",
                    disabled: vm.isBusyAction,
                    action: { vm.runDreamPass() }
                )

                Spacer()

                Button(action: { vm.refreshNow() }) {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(GlassPalette.secondaryLabel)
                        .frame(width: 26, height: 26)
                        .background(
                            Circle()
                                .fill(GlassPalette.microCapsuleFill(colorScheme))
                        )
                        .overlay(
                            Circle()
                                .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.55)
                        )
                }
                .buttonStyle(.plain)
                .help("刷新状态")

                Button(action: { NSApplication.shared.terminate(nil) }) {
                    Text("退出")
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(GlassPalette.secondaryLabel)
                        .padding(.horizontal, 9)
                        .padding(.vertical, 5)
                        .background(
                            Capsule()
                                .fill(GlassPalette.microCapsuleFill(colorScheme))
                        )
                        .overlay(
                            Capsule()
                                .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.55)
                        )
                }
                .buttonStyle(.plain)
            }
        }
    }
}

// MARK: - Floating Liquid Glass Panel & AppKit Status Bar Controller

final class FloatingGlassPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private let panelSize = NSSize(width: 420, height: 556)
    private var statusItem: NSStatusItem!
    private var panel: FloatingGlassPanel!
    private var viewModel: DashboardViewModel!
    private var globalClickMonitor: Any?
    private var localKeyMonitor: Any?

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
            button.action = #selector(togglePanel(_:))
            button.toolTip = "Clone Yourself · 本地知识实时状态"
        }

        buildFloatingGlassPanel()

        viewModel.onStatusBarChange = { [weak self] snap in
            self?.updateStatusButton(snap)
        }
        viewModel.startPolling()
    }

    private func buildFloatingGlassPanel() {
        let rect = NSRect(origin: .zero, size: panelSize)
        panel = FloatingGlassPanel(
            contentRect: rect,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = true
        panel.level = .popUpMenu
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient, .ignoresCycle]
        panel.isMovableByWindowBackground = false
        panel.delegate = self

        let hostingView = NSHostingView(rootView: DashboardPopoverView(vm: viewModel))
        hostingView.frame = rect
        hostingView.autoresizingMask = [.width, .height]

        #if compiler(>=6.2)
        if #available(macOS 26.0, *) {
            let glassRoot = NSGlassEffectView(frame: rect)
            glassRoot.cornerRadius = 26
            glassRoot.style = .regular
            glassRoot.autoresizingMask = [.width, .height]
            glassRoot.contentView = hostingView
            panel.contentView = glassRoot
            return
        }
        #endif

        let fallbackBlur = NSVisualEffectView(frame: rect)
        fallbackBlur.material = .hudWindow
        fallbackBlur.blendingMode = .behindWindow
        fallbackBlur.state = .active
        fallbackBlur.wantsLayer = true
        fallbackBlur.layer?.cornerRadius = 26
        fallbackBlur.layer?.cornerCurve = .continuous
        fallbackBlur.layer?.masksToBounds = true
        fallbackBlur.addSubview(hostingView)
        panel.contentView = fallbackBlur
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

    @objc private func togglePanel(_ sender: AnyObject?) {
        if panel.isVisible {
            closePanel()
        } else {
            showPanel()
        }
    }

    private func showPanel() {
        guard let button = statusItem.button, let btnWindow = button.window else { return }
        let btnRectInWindow = button.convert(button.bounds, to: nil)
        let btnScreenRect = btnWindow.convertToScreen(btnRectInWindow)
        let screenFrame = (btnWindow.screen ?? NSScreen.main)?.visibleFrame
            ?? NSRect(x: 0, y: 0, width: 1440, height: 900)

        var originX = round(btnScreenRect.midX - panelSize.width / 2.0)
        originX = max(screenFrame.minX + 8, min(originX, screenFrame.maxX - panelSize.width - 8))
        let originY = btnScreenRect.minY - panelSize.height - 6

        panel.setFrameOrigin(NSPoint(x: originX, y: originY))
        panel.alphaValue = 0.0
        button.highlight(true)
        panel.makeKeyAndOrderFront(nil)
        viewModel.setPopoverVisible(true)
        installDismissMonitors()

        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.16
            ctx.timingFunction = CAMediaTimingFunction(name: .easeOut)
            panel.animator().alphaValue = 1.0
        }
    }

    private func closePanel() {
        guard panel.isVisible else { return }
        removeDismissMonitors()
        statusItem.button?.highlight(false)
        viewModel.setPopoverVisible(false)
        panel.orderOut(nil)
    }

    private func installDismissMonitors() {
        removeDismissMonitors()
        globalClickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown, .otherMouseDown]
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self = self, self.panel.isVisible else { return }
                let mouseLoc = NSEvent.mouseLocation
                if let btnFrame = self.statusItem.button?.window?.frame, btnFrame.contains(mouseLoc) {
                    return
                }
                if !self.panel.frame.contains(mouseLoc) {
                    self.closePanel()
                }
            }
        }
        localKeyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            if event.keyCode == 53 { // Escape
                Task { @MainActor [weak self] in
                    self?.closePanel()
                }
                return nil
            }
            return event
        }
    }

    private func removeDismissMonitors() {
        if let monitor = globalClickMonitor {
            NSEvent.removeMonitor(monitor)
            globalClickMonitor = nil
        }
        if let monitor = localKeyMonitor {
            NSEvent.removeMonitor(monitor)
            localKeyMonitor = nil
        }
    }

    func windowDidResignKey(_ notification: Notification) {
        closePanel()
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
