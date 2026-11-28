import AppKit
import Combine
import Darwin
import Foundation
import SwiftUI
import UniformTypeIdentifiers

// MARK: - Snapshot Models

struct HostStatusSnapshot: Decodable, Equatable {
    let online: Bool
    let pid: Int?
    let auto_dream: Bool
    let teams_away_only: Bool
    let teams_inbox: Bool
}

struct TotalsSnapshot: Decodable, Equatable {
    let today_hits: Int
    let total_hits: Int
    let memos: Int
    let claims: Int
    let solutions: Int
    let themes: Int
    let open_sessions: Int
    let active_sessions: Int
}

struct GasGaugeSnapshot: Decodable, Equatable {
    let total_saved_tokens: Int
    let total_saved_formatted: String
    let today_saved_tokens: Int
    let today_saved_formatted: String
    let total_injected_tokens: Int
    let today_injected_tokens: Int
    let reused_acquisition_tokens: Int
    let reused_acquisition_formatted: String
    let roi_multiple: Double
    let fill_ratio: Double
    let next_milestone_tokens: Int
    let next_milestone_formatted: String
}

struct SessionKnowledgeItem: Decodable, Identifiable, Equatable {
    var id: String { "\(kind):\(subject)" }
    let kind: String
    let subject: String
    let workspace: String?
    let title: String
    let event: String
    let time_ago: String
    let saved_tokens: Int?
    let saved_formatted: String?
    let acquisition_tokens: Int?
    let acquisition_formatted: String?
}

struct LiveSessionItem: Decodable, Identifiable, Equatable {
    var id: String { session }
    let session: String
    let session_short: String
    let client: String
    let workspace: String
    let title: String
    let state: String
    let last_query: String
    let knowledge_used: [SessionKnowledgeItem]
    let saved_tokens: Int?
    let saved_formatted: String?
    let updated_ago: String
}

struct ActivityFeedItem: Decodable, Identifiable, Equatable {
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
    let saved_tokens: Int?
    let acquisition_tokens: Int?
    let token_badge: String?
}

struct MenuBarSnapshot: Decodable, Equatable {
    let schema: String
    let updated_at: String
    let machine_name: String
    let primary_workspace: String?
    let just_hit_knowledge: Bool
    let last_hit_ago: String
    let host: HostStatusSnapshot
    let totals: TotalsSnapshot
    let gas_gauge: GasGaugeSnapshot
    var live_sessions: [LiveSessionItem]
    let recent_activity: [ActivityFeedItem]

    func hasSameVisibleContent(as other: MenuBarSnapshot?) -> Bool {
        guard let other = other else { return false }
        return machine_name == other.machine_name
            && primary_workspace == other.primary_workspace
            && just_hit_knowledge == other.just_hit_knowledge
            && last_hit_ago == other.last_hit_ago
            && host == other.host
            && totals == other.totals
            && gas_gauge == other.gas_gauge
            && live_sessions == other.live_sessions
            && recent_activity == other.recent_activity
    }
}

struct KnowledgeDetailSection: Decodable, Identifiable {
    var id: String { label }
    let label: String
    let icon: String
    let body: String
}

struct KnowledgeAnchorDetail: Decodable, Identifiable {
    var id: String { "\(path):\(sha256_short)" }
    let path: String
    let full_path: String?
    let sha256_short: String
    let symbols: [String]
    let status: String
    let status_label: String
}

struct KnowledgeUsageSummary: Decodable {
    let hit_count: Int
    let last_hit_ago: String
    let updated_ago: String
}

struct KnowledgeTokenSummary: Decodable {
    let acquisition_tokens: Int
    let acquisition_formatted: String
    let injected_tokens: Int
    let saved_per_hit: Int
    let saved_per_hit_formatted: String
    let cumulative_saved_tokens: Int
    let cumulative_saved_formatted: String
    let basis: String
    let basis_label: String
}

struct KnowledgeItemDetail: Decodable {
    let schema: String
    let kind: String
    let badge: String
    let id: String
    let workspace: String
    let title: String
    let version: Int
    let lifecycle: String
    let integrity_status: String
    let integrity_label: String
    let tags: [String]
    let tokens: KnowledgeTokenSummary
    let sections: [KnowledgeDetailSection]
    let anchors: [KnowledgeAnchorDetail]
    let usage: KnowledgeUsageSummary
    let copy_markdown: String
}

struct SelectedKnowledgeRef: Equatable {
    let kind: String
    let subject: String
    let workspace: String?
    let fallbackTitle: String

    var cacheKey: String {
        "\(kind):\(workspace ?? ""):\(subject)"
    }
}

struct HitsByKind: Decodable {
    let memo: Int
    let claim: Int
    let solution: Int
}

struct HitsChannelStat: Decodable, Identifiable {
    var id: String { key }
    let key: String
    let label: String
    let count: Int
}

struct HitsTopItem: Decodable, Identifiable {
    var id: String { "\(kind):\(workspace):\(subject)" }
    let kind: String
    let badge: String
    let subject: String
    let workspace: String
    let title: String
    let hit_count: Int
    let sessions_count: Int
    let last_hit_ago: String
    let acquisition_tokens: Int
    let acquisition_formatted: String
    let window_saved_tokens: Int
    let window_saved_formatted: String
}

struct HitsEventItem: Decodable, Identifiable {
    let id: String
    let kind: String
    let badge: String
    let subject: String
    let workspace: String
    let title: String
    let version: Int
    let channel_key: String
    let channel_label: String
    let session_short: String
    let task_title: String
    let query: String
    let local_time: String
    let time_ago: String
    let occurred_at: String
    let saved_tokens: Int
    let saved_formatted: String
    let acquisition_formatted: String
}

struct HitsBreakdownReport: Decodable {
    let schema: String
    let window_hours: Int
    let window_hits: Int
    let total_hits: Int
    let window_saved_tokens: Int
    let window_saved_formatted: String
    let total_saved_tokens: Int
    let total_saved_formatted: String
    let window_injected_tokens: Int
    let roi_multiple: Double
    let distinct_items: Int
    let distinct_sessions: Int
    let by_kind: HitsByKind
    let channels: [HitsChannelStat]
    let top_items: [HitsTopItem]
    let events: [HitsEventItem]
    let recent_fallback_events: [HitsEventItem]
    let copy_markdown: String
}

struct BundleExportCounts: Decodable {
    let memos: Int
    let claims: Int
    let solutions: Int
    let themes: Int
    let acquisition_tokens: Int
}

struct BundleExportManifest: Decodable {
    let workspace: String
    let counts: BundleExportCounts
}

struct BundleExportResponse: Decodable {
    let status: String
    let bundle_path: String
    let size_bytes: Int
    let manifest: BundleExportManifest
}

struct BundleImportCounts: Decodable {
    let imported: Int
    let fast_forwarded: Int
    let noop: Int
    let conflicts: Int
    let applicable: Int
    let soft_drift: Int
    let branch_mismatch: Int
    let inherited_acquisition_tokens: Int
}

struct BundleImportResponse: Decodable {
    let status: String
    let workspace: String
    let origin_machine: String
    let counts: BundleImportCounts
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
    @Published var selectedRef: SelectedKnowledgeRef? = nil
    @Published var selectedDetail: KnowledgeItemDetail? = nil
    @Published var isLoadingDetail: Bool = false
    @Published var detailError: String? = nil
    @Published var showHitsBreakdown: Bool = false
    @Published var hitsSubTab: Int = 0
    @Published var hitsReport: HitsBreakdownReport? = nil
    @Published var isLoadingHits: Bool = false
    @Published var hitsError: String? = nil

    let cli: RuntimeCLI
    var onStatusBarChange: ((MenuBarSnapshot?) -> Void)?
    var onSuspendAutoDismiss: ((Bool) -> Void)?
    private var timer: Timer?
    private var fetchInFlight = false
    private var detailCache: [String: KnowledgeItemDetail] = [:]
    private var pinnedSessionOrder: [String] = []

    var isShowingDetail: Bool {
        selectedRef != nil
    }

    init(cli: RuntimeCLI) {
        self.cli = cli
    }

    func startPolling() {
        refreshNow(allowReorder: true)
        scheduleTimer(interval: 8.0)
    }

    func setPopoverVisible(_ visible: Bool) {
        popoverVisible = visible
        if visible {
            pinnedSessionOrder.removeAll()
            refreshNow(allowReorder: true)
            scheduleTimer(interval: 2.5)
        } else {
            pinnedSessionOrder.removeAll()
            scheduleTimer(interval: 8.0)
        }
    }

    private func scheduleTimer(interval: TimeInterval) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self = self else { return }
                self.refreshNow(allowReorder: !self.popoverVisible)
            }
        }
    }

    private func stabilizeSessionOrder(_ incoming: [LiveSessionItem], allowReorder: Bool) -> [LiveSessionItem] {
        if allowReorder || pinnedSessionOrder.isEmpty {
            pinnedSessionOrder = incoming.map(\.session)
            return incoming
        }
        var byId: [String: LiveSessionItem] = [:]
        for item in incoming {
            byId[item.session] = item
        }
        var ordered: [LiveSessionItem] = []
        var keptIds: [String] = []
        for sid in pinnedSessionOrder {
            if let existing = byId.removeValue(forKey: sid) {
                ordered.append(existing)
                keptIds.append(sid)
            }
        }
        for item in incoming where byId[item.session] != nil {
            ordered.append(item)
            keptIds.append(item.session)
        }
        pinnedSessionOrder = keptIds
        return ordered
    }

    func refreshNow(allowReorder: Bool = true) {
        guard !fetchInFlight else { return }
        fetchInFlight = true
        let localCLI = cli
        let needHits = showHitsBreakdown
        DispatchQueue.global(qos: .utility).async { [weak self] in
            let data = localCLI.executeJSON(["menubar-snapshot"])
            let decoded: MenuBarSnapshot?
            if let data = data {
                decoded = try? JSONDecoder().decode(MenuBarSnapshot.self, from: data)
            } else {
                decoded = nil
            }
            var refreshedHits: HitsBreakdownReport? = nil
            if needHits, let hData = localCLI.executeJSON(["menubar-hits", "--hours", "24"]) {
                refreshedHits = try? JSONDecoder().decode(HitsBreakdownReport.self, from: hData)
            }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.fetchInFlight = false
                if var decoded = decoded {
                    decoded.live_sessions = self.stabilizeSessionOrder(
                        decoded.live_sessions,
                        allowReorder: allowReorder
                    )
                    self.onStatusBarChange?(decoded)
                    if !decoded.hasSameVisibleContent(as: self.snapshot) {
                        var txn = Transaction()
                        txn.disablesAnimations = true
                        withTransaction(txn) {
                            self.snapshot = decoded
                        }
                    }
                }
                if let refreshedHits = refreshedHits {
                    self.hitsReport = refreshedHits
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

    func shareBundleViaAirDrop() {
        guard !isBusyAction else { return }
        guard let ws = snapshot?.primary_workspace, !ws.isEmpty else {
            showTemporaryToast("暂无已注册工作区可导出")
            return
        }
        isBusyAction = true
        toastMessage = "正在打包 .noomesh 可共享经验包…"
        let localCLI = cli
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = localCLI.executeJSON(["bundle-export", "--workspace", ws, "--approve"])
            let decoded: BundleExportResponse? = data.flatMap {
                try? JSONDecoder().decode(BundleExportResponse.self, from: $0)
            }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.isBusyAction = false
                guard let exp = decoded else {
                    self.showTemporaryToast("导出 .noomesh 经验包失败")
                    return
                }
                let fileURL = URL(fileURLWithPath: exp.bundle_path)
                let totalItems = exp.manifest.counts.memos + exp.manifest.counts.claims + exp.manifest.counts.solutions
                self.onSuspendAutoDismiss?(true)
                if let airDrop = NSSharingService(named: .sendViaAirDrop) {
                    airDrop.perform(withItems: [fileURL])
                    self.showTemporaryToast("已唤起 AirDrop（含 \(totalItems) 条经验契约）")
                } else {
                    NSWorkspace.shared.activateFileViewerSelecting([fileURL])
                    self.showTemporaryToast("已在访达选中 .noomesh 包，可右键隔空投送")
                }
                DispatchQueue.main.asyncAfter(deadline: .now() + 2.0) { [weak self] in
                    self?.onSuspendAutoDismiss?(false)
                }
            }
        }
    }

    func pickAndImportBundle() {
        guard !isBusyAction else { return }
        onSuspendAutoDismiss?(true)
        let openPanel = NSOpenPanel()
        openPanel.title = "选择要导入的 .noomesh 经验包"
        openPanel.message = "NooMesh 将自动对照本机当前分支源码完成重绑定与符号检定"
        openPanel.prompt = "导入并检定"
        openPanel.canChooseFiles = true
        openPanel.canChooseDirectories = false
        openPanel.allowsMultipleSelection = false
        let downloadsURL = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
        if let downloadsURL = downloadsURL {
            openPanel.directoryURL = downloadsURL
        }
        if let uti = UTType(filenameExtension: "noomesh") {
            openPanel.allowedContentTypes = [uti, .gzip, .data]
        }
        NSApplication.shared.activate(ignoringOtherApps: true)
        let response = openPanel.runModal()
        onSuspendAutoDismiss?(false)
        guard response == .OK, let url = openPanel.url else { return }
        importBundleFile(at: url)
    }

    func importBundleFile(at url: URL) {
        guard !isBusyAction else { return }
        isBusyAction = true
        toastMessage = "正在对照本机源码重检定并导入 \(url.lastPathComponent)…"
        let localCLI = cli
        let ws = snapshot?.primary_workspace
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var args = ["bundle-import", url.path, "--approve"]
            if let ws = ws, !ws.isEmpty {
                args += ["--workspace", ws]
            }
            let data = localCLI.executeJSON(args)
            let decoded: BundleImportResponse? = data.flatMap {
                try? JSONDecoder().decode(BundleImportResponse.self, from: $0)
            }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.isBusyAction = false
                if let imp = decoded {
                    self.detailCache.removeAll()
                    let c = imp.counts
                    let added = c.imported + c.fast_forwarded
                    var parts: [String] = ["同步 +\(added) 条"]
                    if c.applicable > 0 { parts.append("\(c.applicable) 可用") }
                    if c.soft_drift > 0 { parts.append("\(c.soft_drift) 软漂移") }
                    if c.branch_mismatch > 0 { parts.append("\(c.branch_mismatch) 跨分支休眠") }
                    if c.conflicts > 0 { parts.append("\(c.conflicts) 待裁决") }
                    if added == 0 && c.noop > 0 { parts = ["已是最新（\(c.noop) 条一致）"] }
                    self.showTemporaryToast("导入完成：" + parts.joined(separator: " · "))
                    self.refreshNow(allowReorder: true)
                } else {
                    self.showTemporaryToast("导入失败：请确认本机已注册对应工作区")
                }
            }
        }
    }

    func openKnowledgeDetail(kind: String, subject: String, workspace: String?, fallbackTitle: String) {
        let cleanWS = (workspace?.isEmpty == false) ? workspace : nil
        let ref = SelectedKnowledgeRef(
            kind: kind,
            subject: subject,
            workspace: cleanWS,
            fallbackTitle: fallbackTitle
        )
        detailError = nil
        if let cached = detailCache[ref.cacheKey] {
            selectedDetail = cached
            isLoadingDetail = false
        } else {
            selectedDetail = nil
            isLoadingDetail = true
        }
        withAnimation(.spring(response: 0.28, dampingFraction: 0.85)) {
            selectedRef = ref
        }
        let localCLI = cli
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            var args = ["menubar-item", "--kind", kind, "--subject", subject]
            if let ws = cleanWS {
                args += ["--workspace", ws]
            }
            let data = localCLI.executeJSON(args)
            let decoded: KnowledgeItemDetail?
            if let data = data {
                decoded = try? JSONDecoder().decode(KnowledgeItemDetail.self, from: data)
            } else {
                decoded = nil
            }
            DispatchQueue.main.async {
                guard let self = self, self.selectedRef == ref else { return }
                self.isLoadingDetail = false
                if let decoded = decoded {
                    self.detailCache[ref.cacheKey] = decoded
                    self.selectedDetail = decoded
                } else if self.selectedDetail == nil {
                    self.detailError = "未能读取该条本地知识的完整内容"
                }
            }
        }
    }

    func closeKnowledgeDetail() {
        guard selectedRef != nil else { return }
        withAnimation(.spring(response: 0.26, dampingFraction: 0.86)) {
            selectedRef = nil
        }
    }

    func openHitsBreakdown() {
        hitsError = nil
        if hitsReport == nil {
            isLoadingHits = true
        }
        withAnimation(.spring(response: 0.28, dampingFraction: 0.85)) {
            showHitsBreakdown = true
        }
        let localCLI = cli
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let data = localCLI.executeJSON(["menubar-hits", "--hours", "24"])
            let decoded: HitsBreakdownReport?
            if let data = data {
                decoded = try? JSONDecoder().decode(HitsBreakdownReport.self, from: data)
            } else {
                decoded = nil
            }
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.isLoadingHits = false
                if let decoded = decoded {
                    self.hitsReport = decoded
                } else if self.hitsReport == nil {
                    self.hitsError = "未能读取近 24 小时命中明细"
                }
            }
        }
    }

    func closeHitsBreakdown() {
        guard showHitsBreakdown else { return }
        withAnimation(.spring(response: 0.26, dampingFraction: 0.86)) {
            showHitsBreakdown = false
        }
    }

    func copyHitsReportMarkdown() {
        guard let md = hitsReport?.copy_markdown, !md.isEmpty else { return }
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(md, forType: .string)
        showTemporaryToast("已复制 24h 知识命中报告到剪贴板")
    }

    func resetDetailWithoutAnimation() {
        selectedRef = nil
        detailError = nil
        isLoadingDetail = false
        showHitsBreakdown = false
        hitsError = nil
        isLoadingHits = false
    }

    func copySelectedDetailMarkdown() {
        guard let md = selectedDetail?.copy_markdown, !md.isEmpty else { return }
        let pb = NSPasteboard.general
        pb.clearContents()
        pb.setString(md, forType: .string)
        showTemporaryToast("已复制完整知识内容到剪贴板")
    }

    func openSourceFile(_ fullPath: String) {
        let fileURL = URL(fileURLWithPath: fullPath)
        if let vscodeURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: "com.microsoft.VSCode") {
            let cfg = NSWorkspace.OpenConfiguration()
            NSWorkspace.shared.open([fileURL], withApplicationAt: vscodeURL, configuration: cfg, completionHandler: nil)
        } else {
            NSWorkspace.shared.open(fileURL)
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

// MARK: - Custom NooMesh Throne-Crown, Doctrine-Tome & Contract-Cog Vector Glyph

private struct NooMeshEmblemPaths {
    let solid: CGPath
    let tomeWithLines: CGPath
    let haloCutout: CGPath
    let star: CGPath

    static func build(size sz: CGSize) -> NooMeshEmblemPaths {
        let w = sz.width
        let h = sz.height
        let cx = w * 0.5
        let cy = h * 0.54

        let solid = CGMutablePath()

        // 1. Top Emperor's Golden Throne 3-Spire Crown
        let crownOriginY = cy - h * 0.03
        let rays: [(Double, CGFloat, CGFloat, CGFloat)] = [
            (-90.0, h * 0.16, h * 0.47, w * 0.084),
            (-54.0, h * 0.18, h * 0.38, w * 0.060),
            (-126.0, h * 0.18, h * 0.38, w * 0.060)
        ]
        for (deg, r1, r2, bw) in rays {
            let rad = deg * .pi / 180.0
            let perp = rad + .pi / 2.0
            let b = CGPoint(x: cx + CGFloat(cos(rad)) * r1, y: crownOriginY + CGFloat(sin(rad)) * r1)
            let tip = CGPoint(x: cx + CGFloat(cos(rad)) * r2, y: crownOriginY + CGFloat(sin(rad)) * r2)
            solid.move(to: CGPoint(x: b.x + CGFloat(cos(perp)) * bw * 0.5, y: b.y + CGFloat(sin(perp)) * bw * 0.5))
            solid.addLine(to: tip)
            solid.addLine(to: CGPoint(x: b.x - CGFloat(cos(perp)) * bw * 0.5, y: b.y - CGFloat(sin(perp)) * bw * 0.5))
            solid.closeSubpath()
        }

        // 2. Right Side — Machine-Executable Contract Half-Cog
        let rCenter = CGPoint(x: cx + w * 0.03, y: cy + h * 0.06)
        let cogOuter = min(w, h) * 0.43
        let cogRoot = cogOuter * 0.74
        let cogInner = cogOuter * 0.46
        solid.move(to: CGPoint(x: rCenter.x + w * 0.07, y: rCenter.y - cogRoot * 0.94))
        for deg in [-56.0, -18.0, 20.0, 58.0] {
            let a1 = (deg - 11.5) * .pi / 180.0
            let a2 = (deg - 8.5) * .pi / 180.0
            let a3 = (deg + 8.5) * .pi / 180.0
            let a4 = (deg + 11.5) * .pi / 180.0
            solid.addLine(to: CGPoint(x: rCenter.x + CGFloat(cos(a1)) * cogRoot, y: rCenter.y + CGFloat(sin(a1)) * cogRoot))
            solid.addLine(to: CGPoint(x: rCenter.x + CGFloat(cos(a2)) * cogOuter, y: rCenter.y + CGFloat(sin(a2)) * cogOuter))
            solid.addLine(to: CGPoint(x: rCenter.x + CGFloat(cos(a3)) * cogOuter, y: rCenter.y + CGFloat(sin(a3)) * cogOuter))
            solid.addLine(to: CGPoint(x: rCenter.x + CGFloat(cos(a4)) * cogRoot, y: rCenter.y + CGFloat(sin(a4)) * cogRoot))
        }
        solid.addLine(to: CGPoint(x: rCenter.x + w * 0.07, y: rCenter.y + cogRoot * 0.94))
        solid.addLine(to: CGPoint(x: rCenter.x + w * 0.07, y: rCenter.y + cogInner * 0.92))
        for deg in stride(from: 72.0, through: -72.0, by: -9.0) {
            let rad = deg * .pi / 180.0
            solid.addLine(to: CGPoint(x: rCenter.x + CGFloat(cos(rad)) * cogInner, y: rCenter.y + CGFloat(sin(rad)) * cogInner))
        }
        solid.closeSubpath()

        // 3. Central Spine — Golden Throne Pillar & Pedestal
        let spineW = max(1.5, w * 0.085)
        solid.addRoundedRect(
            in: CGRect(x: cx - spineW * 0.5, y: cy - h * 0.16, width: spineW, height: h * 0.52),
            cornerWidth: spineW * 0.4,
            cornerHeight: spineW * 0.4
        )
        solid.move(to: CGPoint(x: cx - w * 0.13, y: cy + h * 0.38))
        solid.addLine(to: CGPoint(x: cx + w * 0.13, y: cy + h * 0.38))
        solid.addLine(to: CGPoint(x: cx + w * 0.07, y: cy + h * 0.31))
        solid.addLine(to: CGPoint(x: cx - w * 0.07, y: cy + h * 0.31))
        solid.closeSubpath()

        // 4. Left Side — Open Illuminated Doctrine Tome Page with 2 Negative-Space Lines
        let tome = CGMutablePath()
        tome.move(to: CGPoint(x: cx - w * 0.11, y: cy - h * 0.14))
        tome.addQuadCurve(to: CGPoint(x: cx - w * 0.46, y: cy - h * 0.10), control: CGPoint(x: cx - w * 0.28, y: cy - h * 0.18))
        tome.addLine(to: CGPoint(x: cx - w * 0.42, y: cy + h * 0.25))
        tome.addQuadCurve(to: CGPoint(x: cx - w * 0.11, y: cy + h * 0.27), control: CGPoint(x: cx - w * 0.26, y: cy + h * 0.22))
        tome.closeSubpath()

        let lineH = max(1.0, h * 0.054)
        for ly in [cy - h * 0.01, cy + h * 0.10] {
            tome.addRoundedRect(
                in: CGRect(x: cx - w * 0.37, y: ly - lineH * 0.5, width: w * 0.20, height: lineH),
                cornerWidth: lineH * 0.5,
                cornerHeight: lineH * 0.5
            )
        }

        // 5. Central Halo Cutout & Unified Session Astral Spark
        let corePt = CGPoint(x: cx, y: cy + h * 0.06)
        let haloR = min(w, h) * 0.165
        let halo = CGMutablePath()
        halo.addEllipse(in: CGRect(x: corePt.x - haloR, y: corePt.y - haloR, width: haloR * 2.0, height: haloR * 2.0))

        let d = haloR * 0.82
        let k = d * 0.28
        let star = CGMutablePath()
        star.move(to: CGPoint(x: corePt.x, y: corePt.y - d))
        star.addQuadCurve(to: CGPoint(x: corePt.x + d, y: corePt.y), control: CGPoint(x: corePt.x + k, y: corePt.y - k))
        star.addQuadCurve(to: CGPoint(x: corePt.x, y: corePt.y + d), control: CGPoint(x: corePt.x + k, y: corePt.y + k))
        star.addQuadCurve(to: CGPoint(x: corePt.x - d, y: corePt.y), control: CGPoint(x: corePt.x - k, y: corePt.y + k))
        star.addQuadCurve(to: CGPoint(x: corePt.x, y: corePt.y - d), control: CGPoint(x: corePt.x - k, y: corePt.y - k))
        star.closeSubpath()

        return NooMeshEmblemPaths(solid: solid, tomeWithLines: tome, haloCutout: halo, star: star)
    }
}

struct NooMeshGlyphView: View {
    let size: CGFloat

    var body: some View {
        Canvas { ctx, sz in
            let parts = NooMeshEmblemPaths.build(size: sz)
            ctx.drawLayer { layer in
                layer.fill(Path(parts.solid), with: .foreground)
                layer.fill(Path(parts.tomeWithLines), with: .foreground, style: FillStyle(eoFill: true))
                layer.blendMode = .destinationOut
                layer.fill(Path(parts.haloCutout), with: .color(.black))
                layer.blendMode = .normal
                layer.fill(Path(parts.star), with: .foreground)
            }
        }
        .frame(width: size * 1.12, height: size)
    }
}

func makeNooMeshStatusBarImage(pointSize: CGFloat = 18.5) -> NSImage {
    let size = NSSize(width: round(pointSize * 1.12 * 2.0) / 2.0, height: pointSize)
    let image = NSImage(size: size, flipped: true) { rect in
        guard let cg = NSGraphicsContext.current?.cgContext else { return false }
        let parts = NooMeshEmblemPaths.build(size: rect.size)

        cg.beginTransparencyLayer(auxiliaryInfo: nil)
        cg.setFillColor(NSColor.black.cgColor)
        cg.addPath(parts.solid)
        cg.fillPath()

        cg.addPath(parts.tomeWithLines)
        cg.fillPath(using: .evenOdd)

        cg.setBlendMode(.destinationOut)
        cg.addPath(parts.haloCutout)
        cg.fillPath()

        cg.setBlendMode(.normal)
        cg.setFillColor(NSColor.black.cgColor)
        cg.addPath(parts.star)
        cg.fillPath()
        cg.endTransparencyLayer()
        return true
    }
    image.isTemplate = true
    image.accessibilityDescription = "NooMesh 本地知识状态"
    return image
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

struct TokenGasGaugeCard: View {
    @Environment(\.colorScheme) private var colorScheme
    @State private var isHovering = false
    let gauge: GasGaugeSnapshot
    let action: () -> Void

    private var clampedFill: CGFloat {
        CGFloat(min(1.0, max(0.0, gauge.fill_ratio)))
    }

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                // 240° Automotive / Apple Watch Style Arc Gas Gauge
                ZStack {
                    Circle()
                        .trim(from: 0.16, to: 0.84)
                        .stroke(
                            GlassPalette.recessedChannelFill(colorScheme),
                            style: StrokeStyle(lineWidth: 5.5, lineCap: .round)
                        )
                        .rotationEffect(.degrees(90))

                    Circle()
                        .trim(from: 0.16, to: 0.16 + 0.68 * clampedFill)
                        .stroke(
                            LinearGradient(
                                colors: colorScheme == .dark
                                    ? [Color.white.opacity(0.55), Color.white]
                                    : [Color.black.opacity(0.45), Color.black.opacity(0.88)],
                                startPoint: .leading,
                                endPoint: .trailing
                            ),
                            style: StrokeStyle(lineWidth: 5.5, lineCap: .round)
                        )
                        .rotationEffect(.degrees(90))
                        .animation(.spring(response: 0.5, dampingFraction: 0.8), value: clampedFill)

                    VStack(spacing: 0.5) {
                        Image(systemName: "gauge.with.dots.needle.67percent")
                            .font(.system(size: 13.5, weight: .semibold))
                            .foregroundColor(GlassPalette.primaryLabel)
                        Text(String(format: "%.1f×", gauge.roi_multiple))
                            .font(.system(size: 8.5, weight: .semibold, design: .rounded).monospacedDigit())
                            .foregroundColor(GlassPalette.secondaryLabel)
                    }
                }
                .frame(width: 48, height: 48)

                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 5) {
                        Text("经验复用节省 Token 表盘")
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundColor(GlassPalette.secondaryLabel)
                        Spacer()
                        Text("目标 \(gauge.next_milestone_formatted)")
                            .font(.system(size: 9.5, design: .monospaced))
                            .foregroundColor(GlassPalette.tertiaryLabel)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 8.5, weight: .semibold))
                            .foregroundColor(isHovering ? GlassPalette.primaryLabel : GlassPalette.tertiaryLabel)
                    }

                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text("~\(gauge.total_saved_formatted)")
                            .font(.system(size: 20, weight: .bold, design: .rounded).monospacedDigit())
                            .foregroundColor(GlassPalette.primaryLabel)
                        Text("Tokens 累计净省")
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundColor(GlassPalette.secondaryLabel)
                        Spacer()
                    }

                    HStack(spacing: 5.5) {
                        gaugeMicroPill("24h 省 +\(gauge.today_saved_formatted)")
                        gaugeMicroPill("获取投入 ~\(gauge.reused_acquisition_formatted)")
                        gaugeMicroPill(String(format: "回报 %.1f×", gauge.roi_multiple))
                    }
                }
            }
            .padding(.horizontal, 11.5)
            .padding(.vertical, 9.5)
            .liquidDroplet(cornerRadius: 16, emphasized: isHovering, activeRim: true)
            .scaleEffect(isHovering ? 1.012 : 1.0)
            .animation(.easeOut(duration: 0.14), value: isHovering)
            .contentShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            isHovering = hovering
        }
        .help("点击查看 Token 节省账本与近 24 小时逐条命中明细")
    }

    private func gaugeMicroPill(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 9.5, weight: .medium, design: .rounded).monospacedDigit())
            .foregroundColor(GlassPalette.secondaryLabel)
            .padding(.horizontal, 6.5)
            .padding(.vertical, 1.5)
            .background(
                Capsule()
                    .fill(GlassPalette.microCapsuleFill(colorScheme))
            )
            .overlay(
                Capsule()
                    .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.5)
            )
    }
}

struct MetricCard: View {
    @State private var isHovering = false
    let title: String
    let value: String
    let subtitle: String
    var action: (() -> Void)? = nil

    private var cardContent: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 3) {
                Text(title)
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundColor(GlassPalette.secondaryLabel)
                    .lineLimit(1)
                if action != nil {
                    Spacer(minLength: 2)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 8.5, weight: .semibold))
                        .foregroundColor(isHovering ? GlassPalette.primaryLabel : GlassPalette.tertiaryLabel)
                }
            }
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
        .liquidDroplet(
            cornerRadius: 14,
            emphasized: action != nil && isHovering,
            activeRim: action != nil && isHovering
        )
        .scaleEffect(action != nil && isHovering ? 1.02 : 1.0)
        .animation(.easeOut(duration: 0.14), value: isHovering)
        .contentShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    var body: some View {
        if let action = action {
            Button(action: action) {
                cardContent
            }
            .buttonStyle(.plain)
            .onHover { hovering in
                isHovering = hovering
            }
            .help("点击查看近 24 小时知识命中统计与逐条明细")
        } else {
            cardContent
        }
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

struct SessionKnowledgeRowButton: View {
    @Environment(\.colorScheme) private var colorScheme
    @State private var isHovering = false
    let item: SessionKnowledgeItem
    let fallbackWorkspace: String
    let onSelect: (String, String, String?, String) -> Void

    var body: some View {
        Button(action: {
            let ws = (item.workspace?.isEmpty == false) ? item.workspace : fallbackWorkspace
            onSelect(item.kind, item.subject, ws, item.title)
        }) {
            HStack(alignment: .top, spacing: 5.5) {
                KnowledgeKindTag(kind: item.kind)
                    .padding(.top, 1)
                Text(item.title)
                    .font(.system(size: 11))
                    .foregroundColor(GlassPalette.primaryLabel)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 4)
                HStack(spacing: 4.5) {
                    if let saved = item.saved_formatted {
                        Text("省 ~\(saved)")
                            .font(.system(size: 9.5, weight: .medium, design: .rounded).monospacedDigit())
                            .foregroundColor(GlassPalette.secondaryLabel)
                            .padding(.horizontal, 5.5)
                            .padding(.vertical, 1.5)
                            .background(
                                Capsule()
                                    .fill(GlassPalette.microCapsuleFill(colorScheme))
                            )
                    }
                    Text(item.time_ago)
                        .font(.system(size: 10))
                        .foregroundColor(GlassPalette.tertiaryLabel)
                    Image(systemName: "chevron.right")
                        .font(.system(size: 8.5, weight: .semibold))
                        .foregroundColor(isHovering ? GlassPalette.primaryLabel : GlassPalette.tertiaryLabel)
                }
                .padding(.top, 1)
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 3.5)
            .background(
                RoundedRectangle(cornerRadius: 7.5, style: .continuous)
                    .fill(
                        isHovering
                            ? (colorScheme == .dark ? Color.white.opacity(0.11) : Color.white.opacity(0.62))
                            : Color.clear
                    )
            )
            .contentShape(RoundedRectangle(cornerRadius: 7.5, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            isHovering = hovering
        }
        .help("点击查看该条本地知识的完整内容")
    }
}

struct SessionCardView: View {
    @Environment(\.colorScheme) private var colorScheme
    let item: LiveSessionItem
    let onSelectKnowledge: (String, String, String?, String) -> Void

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
                VStack(alignment: .leading, spacing: 2.5) {
                    HStack(spacing: 4.5) {
                        Image(systemName: "sparkles")
                            .font(.system(size: 9.5, weight: .medium))
                            .foregroundColor(GlassPalette.secondaryLabel)
                        Text("已调用本地知识 (\(item.knowledge_used.count))")
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundColor(GlassPalette.secondaryLabel)
                        Spacer()
                        if let sessSaved = item.saved_formatted, (item.saved_tokens ?? 0) > 0 {
                            Text("本会话共省 ~\(sessSaved) tok")
                                .font(.system(size: 9.5, weight: .semibold, design: .rounded).monospacedDigit())
                                .foregroundColor(GlassPalette.primaryLabel)
                        }
                    }
                    .padding(.bottom, 1.5)
                    ForEach(item.knowledge_used) { k in
                        SessionKnowledgeRowButton(
                            item: k,
                            fallbackWorkspace: item.workspace,
                            onSelect: onSelectKnowledge
                        )
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
    @Environment(\.colorScheme) private var colorScheme
    @State private var isHovering = false
    let item: ActivityFeedItem
    let onSelect: (String, String, String?, String) -> Void

    var isLearned: Bool { item.event_type == "learned" }

    var body: some View {
        Button(action: {
            onSelect(item.kind, item.subject, item.workspace, item.title)
        }) {
            VStack(alignment: .leading, spacing: 5.5) {
                HStack(spacing: 5.5) {
                    HStack(spacing: 3.5) {
                        Image(systemName: isLearned ? "checkmark.circle.fill" : "arrow.turn.down.right")
                            .font(.system(size: 9.5, weight: .medium))
                        Text(item.action)
                            .font(.system(size: 10.5, weight: .medium))
                    }
                    .foregroundColor(GlassPalette.secondaryLabel)

                    KnowledgeKindTag(kind: item.kind)

                    if let tokBadge = item.token_badge, !tokBadge.isEmpty {
                        Text(tokBadge)
                            .font(.system(size: 9.5, weight: .medium, design: .rounded).monospacedDigit())
                            .foregroundColor(GlassPalette.primaryLabel)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 1.5)
                            .background(
                                Capsule()
                                    .fill(GlassPalette.microCapsuleFill(colorScheme))
                            )
                            .overlay(
                                Capsule()
                                    .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.5)
                            )
                    }

                    Spacer()

                    Text(item.time_ago)
                        .font(.system(size: 10))
                        .foregroundColor(GlassPalette.tertiaryLabel)

                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(isHovering ? GlassPalette.primaryLabel : GlassPalette.tertiaryLabel)
                }

                Text(item.title)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(GlassPalette.primaryLabel)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

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
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(11)
            .liquidDroplet(cornerRadius: 15, emphasized: isHovering, activeRim: isHovering)
            .contentShape(RoundedRectangle(cornerRadius: 15, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            isHovering = hovering
        }
        .help("点击查看完整知识经验")
    }
}

struct KnowledgeDetailView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject var vm: DashboardViewModel

    private func integrityDotColor(_ status: String) -> Color {
        switch status {
        case "PASS":
            return GlassPalette.onlineDot
        case "SOFT_DRIFT":
            return Color(nsColor: .systemOrange)
        default:
            return Color(nsColor: .systemRed)
        }
    }

    var body: some View {
        VStack(spacing: 10) {
            HStack(spacing: 8) {
                LiquidCapsuleButton(
                    title: vm.showHitsBreakdown ? "返回 24h 明细" : "返回列表",
                    systemImage: "chevron.left",
                    disabled: false,
                    action: { vm.closeKnowledgeDetail() }
                )

                Spacer()

                if vm.selectedDetail != nil {
                    LiquidCapsuleButton(
                        title: "复制全文",
                        systemImage: "doc.on.doc",
                        disabled: false,
                        action: { vm.copySelectedDetailMarkdown() }
                    )
                }
            }

            if let detail = vm.selectedDetail {
                ScrollView(.vertical, showsIndicators: false) {
                    VStack(alignment: .leading, spacing: 9) {
                        overviewCard(detail)
                        tokenLedgerCard(detail.tokens)

                        ForEach(detail.sections) { sec in
                            sectionCard(sec)
                        }

                        if !detail.anchors.isEmpty {
                            anchorsCard(detail.anchors)
                        }
                    }
                    .padding(.vertical, 2)
                }
            } else if vm.isLoadingDetail {
                Spacer()
                ProgressView("正在读取完整知识经验…")
                    .font(.system(size: 12))
                Spacer()
            } else {
                Spacer()
                VStack(spacing: 8) {
                    Image(systemName: "exclamationmark.circle")
                        .font(.system(size: 24, weight: .light))
                        .foregroundColor(GlassPalette.tertiaryLabel)
                    Text(vm.detailError ?? "未找到该条本地知识")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(GlassPalette.secondaryLabel)
                }
                Spacer()
            }
        }
    }

    private func overviewCard(_ detail: KnowledgeItemDetail) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 6) {
                KnowledgeKindTag(kind: detail.kind)
                Text(detail.workspace)
                    .font(.system(size: 10.5))
                    .foregroundColor(GlassPalette.secondaryLabel)
                    .lineLimit(1)
                Text("v\(detail.version)")
                    .font(.system(size: 10, weight: .medium, design: .monospaced))
                    .foregroundColor(GlassPalette.tertiaryLabel)
                Spacer()
                HStack(spacing: 4.5) {
                    Circle()
                        .fill(integrityDotColor(detail.integrity_status))
                        .frame(width: 6, height: 6)
                    Text(detail.integrity_label)
                        .font(.system(size: 10))
                        .foregroundColor(GlassPalette.secondaryLabel)
                        .lineLimit(1)
                }
            }

            Text(detail.title)
                .font(.system(size: 13.5, weight: .semibold))
                .foregroundColor(GlassPalette.primaryLabel)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

            HStack(spacing: 6) {
                Text("ID: \(detail.id)")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundColor(GlassPalette.tertiaryLabel)
                    .lineLimit(1)
                    .textSelection(.enabled)
                Spacer()
                Text("累计调用 \(detail.usage.hit_count) 次 · 最近 \(detail.usage.last_hit_ago)")
                    .font(.system(size: 10))
                    .foregroundColor(GlassPalette.tertiaryLabel)
            }

            if !detail.tags.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 5) {
                        ForEach(detail.tags, id: \.self) { tag in
                            Text(tag)
                                .font(.system(size: 9.5, weight: .medium))
                                .foregroundColor(GlassPalette.secondaryLabel)
                                .padding(.horizontal, 6.5)
                                .padding(.vertical, 2)
                                .background(
                                    Capsule()
                                        .fill(GlassPalette.microCapsuleFill(colorScheme))
                                )
                                .overlay(
                                    Capsule()
                                        .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.5)
                                )
                        }
                    }
                }
            }
        }
        .padding(12)
        .liquidDroplet(cornerRadius: 16, emphasized: true, activeRim: true, interactiveHover: false)
    }

    private func tokenLedgerCard(_ tok: KnowledgeTokenSummary) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 5) {
                Image(systemName: "gauge.with.dots.needle.67percent")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(GlassPalette.secondaryLabel)
                Text("Token 节省账本")
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundColor(GlassPalette.secondaryLabel)
                Spacer()
                Text(tok.basis_label)
                    .font(.system(size: 9.5, weight: .medium))
                    .foregroundColor(GlassPalette.tertiaryLabel)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1.5)
                    .background(
                        Capsule()
                            .fill(GlassPalette.recessedChannelFill(colorScheme))
                    )
            }

            HStack(spacing: 6) {
                ledgerCell(label: "首次获取耗", value: "~\(tok.acquisition_formatted)")
                ledgerCell(label: "单次注入", value: "\(tok.injected_tokens)")
                ledgerCell(label: "单次净省", value: "~\(tok.saved_per_hit_formatted)")
                ledgerCell(label: "累计已省", value: "~\(tok.cumulative_saved_formatted)")
            }
        }
        .padding(11)
        .liquidDroplet(cornerRadius: 15, interactiveHover: false)
    }

    private func ledgerCell(label: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.system(size: 9.5))
                .foregroundColor(GlassPalette.tertiaryLabel)
                .lineLimit(1)
            Text(value)
                .font(.system(size: 13, weight: .semibold, design: .rounded).monospacedDigit())
                .foregroundColor(GlassPalette.primaryLabel)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 7.5)
        .padding(.vertical, 5.5)
        .background(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(GlassPalette.recessedChannelFill(colorScheme))
        )
    }

    private func sectionCard(_ sec: KnowledgeDetailSection) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 5) {
                Image(systemName: sec.icon)
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(GlassPalette.secondaryLabel)
                Text(sec.label)
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundColor(GlassPalette.secondaryLabel)
                Spacer()
            }

            Text(sec.body)
                .font(.system(size: 11.5, weight: .regular))
                .lineSpacing(2.8)
                .foregroundColor(GlassPalette.primaryLabel)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
        }
        .padding(11.5)
        .liquidDroplet(cornerRadius: 15, interactiveHover: false)
    }

    private func anchorsCard(_ anchors: [KnowledgeAnchorDetail]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 5) {
                Image(systemName: "link")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(GlassPalette.secondaryLabel)
                Text("绑定源码与符号锚点 (\(anchors.count))")
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundColor(GlassPalette.secondaryLabel)
                Spacer()
            }

            ForEach(Array(anchors.enumerated()), id: \.element.id) { index, anchor in
                if index > 0 {
                    Rectangle()
                        .fill(Color.white.opacity(colorScheme == .dark ? 0.10 : 0.45))
                        .frame(height: 0.6)
                }
                VStack(alignment: .leading, spacing: 5) {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(integrityDotColor(anchor.status))
                            .frame(width: 5.5, height: 5.5)
                        Text((anchor.path as NSString).lastPathComponent)
                            .font(.system(size: 11, weight: .semibold))
                            .foregroundColor(GlassPalette.primaryLabel)
                            .lineLimit(1)
                        Text(anchor.status_label)
                            .font(.system(size: 9.5))
                            .foregroundColor(GlassPalette.tertiaryLabel)
                        Spacer()
                        if let fullPath = anchor.full_path {
                            Button(action: { vm.openSourceFile(fullPath) }) {
                                HStack(spacing: 3) {
                                    Image(systemName: "arrow.up.forward.app")
                                        .font(.system(size: 9, weight: .medium))
                                    Text("打开源码")
                                        .font(.system(size: 9.5, weight: .medium))
                                }
                                .foregroundColor(GlassPalette.primaryLabel)
                                .padding(.horizontal, 7)
                                .padding(.vertical, 2.5)
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

                    Text(anchor.path)
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundColor(GlassPalette.tertiaryLabel)
                        .fixedSize(horizontal: false, vertical: true)
                        .textSelection(.enabled)

                    if !anchor.symbols.isEmpty {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 5) {
                                ForEach(anchor.symbols, id: \.self) { sym in
                                    HStack(spacing: 3) {
                                        Image(systemName: "curlybraces")
                                            .font(.system(size: 8))
                                        Text(sym)
                                            .font(.system(size: 9.5, design: .monospaced))
                                    }
                                    .foregroundColor(GlassPalette.secondaryLabel)
                                    .padding(.horizontal, 6)
                                    .padding(.vertical, 2)
                                    .background(
                                        Capsule()
                                            .fill(GlassPalette.recessedChannelFill(colorScheme))
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }
        .padding(11.5)
        .liquidDroplet(cornerRadius: 15, interactiveHover: false)
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

struct HitsEventRowButton: View {
    @Environment(\.colorScheme) private var colorScheme
    @State private var isHovering = false
    let item: HitsEventItem
    let onSelect: (String, String, String?, String) -> Void

    var body: some View {
        Button(action: {
            onSelect(item.kind, item.subject, item.workspace, item.title)
        }) {
            VStack(alignment: .leading, spacing: 5.5) {
                HStack(spacing: 5.5) {
                    Text(item.local_time)
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundColor(GlassPalette.secondaryLabel)

                    Text("(\(item.time_ago))")
                        .font(.system(size: 9.5))
                        .foregroundColor(GlassPalette.tertiaryLabel)

                    Text(item.channel_label)
                        .font(.system(size: 9.5, weight: .medium))
                        .foregroundColor(GlassPalette.secondaryLabel)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1.5)
                        .background(
                            Capsule()
                                .fill(GlassPalette.microCapsuleFill(colorScheme))
                        )
                        .overlay(
                            Capsule()
                                .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.5)
                        )

                    KnowledgeKindTag(kind: item.kind)

                    Text("省 ~\(item.saved_formatted)")
                        .font(.system(size: 9.5, weight: .semibold, design: .rounded).monospacedDigit())
                        .foregroundColor(GlassPalette.primaryLabel)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1.5)
                        .background(
                            Capsule()
                                .fill(GlassPalette.microCapsuleFill(colorScheme))
                        )
                        .overlay(
                            Capsule()
                                .strokeBorder(GlassPalette.specularRimGradient(colorScheme, active: true), lineWidth: 0.5)
                        )

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(.system(size: 9, weight: .semibold))
                        .foregroundColor(isHovering ? GlassPalette.primaryLabel : GlassPalette.tertiaryLabel)
                }

                Text(item.title)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(GlassPalette.primaryLabel)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                if !item.query.isEmpty {
                    HStack(spacing: 4.5) {
                        Image(systemName: "magnifyingglass")
                            .font(.system(size: 9))
                            .foregroundColor(GlassPalette.tertiaryLabel)
                        Text(item.query)
                            .font(.system(size: 10.5))
                            .foregroundColor(GlassPalette.secondaryLabel)
                            .lineLimit(1)
                    }
                }

                HStack(spacing: 4) {
                    Text(item.session_short)
                        .font(.system(size: 9.5, weight: .regular, design: .monospaced))
                        .foregroundColor(GlassPalette.tertiaryLabel)
                    if !item.task_title.isEmpty {
                        Text("·")
                            .foregroundColor(GlassPalette.tertiaryLabel)
                        Text(item.task_title)
                            .font(.system(size: 10))
                            .foregroundColor(GlassPalette.secondaryLabel)
                            .lineLimit(1)
                    } else if !item.workspace.isEmpty {
                        Text("·")
                            .foregroundColor(GlassPalette.tertiaryLabel)
                        Text(item.workspace)
                            .font(.system(size: 10))
                            .foregroundColor(GlassPalette.tertiaryLabel)
                            .lineLimit(1)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10.5)
            .liquidDroplet(cornerRadius: 15, emphasized: isHovering, activeRim: isHovering)
            .contentShape(RoundedRectangle(cornerRadius: 15, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            isHovering = hovering
        }
        .help("点击查看该条本地知识的完整经验内容")
    }
}

struct HitsTopItemRowButton: View {
    @Environment(\.colorScheme) private var colorScheme
    @State private var isHovering = false
    let rank: Int
    let item: HitsTopItem
    let onSelect: (String, String, String?, String) -> Void

    var body: some View {
        Button(action: {
            onSelect(item.kind, item.subject, item.workspace, item.title)
        }) {
            HStack(spacing: 9) {
                Text("#\(rank)")
                    .font(.system(size: 11, weight: .semibold, design: .rounded).monospacedDigit())
                    .foregroundColor(GlassPalette.secondaryLabel)
                    .frame(width: 24, height: 24)
                    .background(
                        Circle()
                            .fill(GlassPalette.microCapsuleFill(colorScheme))
                    )
                    .overlay(
                        Circle()
                            .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.5)
                    )

                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 5.5) {
                        KnowledgeKindTag(kind: item.kind)
                        Text(item.workspace)
                            .font(.system(size: 10))
                            .foregroundColor(GlassPalette.tertiaryLabel)
                            .lineLimit(1)
                        Spacer()
                        Text("命中 \(item.hit_count) 次 · 省 ~\(item.window_saved_formatted)")
                            .font(.system(size: 10, weight: .semibold, design: .rounded).monospacedDigit())
                            .foregroundColor(GlassPalette.primaryLabel)
                            .padding(.horizontal, 7)
                            .padding(.vertical, 2)
                            .background(
                                Capsule()
                                    .fill(GlassPalette.microCapsuleFill(colorScheme))
                            )
                            .overlay(
                                Capsule()
                                    .strokeBorder(GlassPalette.specularRimGradient(colorScheme, active: true), lineWidth: 0.55)
                            )
                    }

                    Text(item.title)
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(GlassPalette.primaryLabel)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)

                    Text("首次获取耗 ~\(item.acquisition_formatted) tok · 赋能 \(item.sessions_count) 个会话 · 最近 \(item.last_hit_ago)")
                        .font(.system(size: 10))
                        .foregroundColor(GlassPalette.tertiaryLabel)
                }

                Image(systemName: "chevron.right")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundColor(isHovering ? GlassPalette.primaryLabel : GlassPalette.tertiaryLabel)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10.5)
            .liquidDroplet(cornerRadius: 15, emphasized: isHovering, activeRim: isHovering)
            .contentShape(RoundedRectangle(cornerRadius: 15, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { hovering in
            isHovering = hovering
        }
        .help("点击查看该条高频知识的完整内容")
    }
}

struct HitsBreakdownView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject var vm: DashboardViewModel

    var body: some View {
        VStack(spacing: 10) {
            HStack(spacing: 8) {
                LiquidCapsuleButton(
                    title: "返回概览",
                    systemImage: "chevron.left",
                    disabled: false,
                    action: { vm.closeHitsBreakdown() }
                )

                Spacer()

                if vm.hitsReport != nil {
                    LiquidCapsuleButton(
                        title: "复制报表",
                        systemImage: "doc.on.doc",
                        disabled: false,
                        action: { vm.copyHitsReportMarkdown() }
                    )
                }
            }

            if let report = vm.hitsReport {
                summaryHeroCard(report)

                LiquidSegmentedBar(
                    selection: $vm.hitsSubTab,
                    leftTitle: "逐条命中明细 (\(report.events.count))",
                    rightTitle: "高频经验排行 (\(report.top_items.count))"
                )

                ScrollView(.vertical, showsIndicators: false) {
                    VStack(spacing: 8) {
                        if vm.hitsSubTab == 0 {
                            if report.events.isEmpty {
                                VStack(spacing: 6) {
                                    Text("近 24 小时暂无新命中记录")
                                        .font(.system(size: 11.5, weight: .medium))
                                        .foregroundColor(GlassPalette.secondaryLabel)
                                    if !report.recent_fallback_events.isEmpty {
                                        Text("以下为最近历史命中记录（点击可查看经验全文）")
                                            .font(.system(size: 10))
                                            .foregroundColor(GlassPalette.tertiaryLabel)
                                    }
                                }
                                .padding(.vertical, 8)

                                ForEach(report.recent_fallback_events) { ev in
                                    HitsEventRowButton(item: ev) { kind, subject, ws, title in
                                        vm.openKnowledgeDetail(
                                            kind: kind,
                                            subject: subject,
                                            workspace: ws,
                                            fallbackTitle: title
                                        )
                                    }
                                }
                            } else {
                                ForEach(report.events) { ev in
                                    HitsEventRowButton(item: ev) { kind, subject, ws, title in
                                        vm.openKnowledgeDetail(
                                            kind: kind,
                                            subject: subject,
                                            workspace: ws,
                                            fallbackTitle: title
                                        )
                                    }
                                }
                            }
                        } else {
                            if report.top_items.isEmpty {
                                VStack(spacing: 6) {
                                    Image(systemName: "chart.bar.xaxis")
                                        .font(.system(size: 22, weight: .light))
                                        .foregroundColor(GlassPalette.tertiaryLabel)
                                    Text("近 24 小时暂无排行数据")
                                        .font(.system(size: 11.5, weight: .medium))
                                        .foregroundColor(GlassPalette.secondaryLabel)
                                }
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 36)
                            } else {
                                ForEach(Array(report.top_items.enumerated()), id: \.element.id) { idx, topItem in
                                    HitsTopItemRowButton(rank: idx + 1, item: topItem) { kind, subject, ws, title in
                                        vm.openKnowledgeDetail(
                                            kind: kind,
                                            subject: subject,
                                            workspace: ws,
                                            fallbackTitle: title
                                        )
                                    }
                                }
                            }
                        }
                    }
                    .padding(.vertical, 2)
                }
            } else if vm.isLoadingHits {
                Spacer()
                ProgressView("正在汇总近 24 小时知识命中明细…")
                    .font(.system(size: 12))
                Spacer()
            } else {
                Spacer()
                Text(vm.hitsError ?? "未能加载命中明细")
                    .font(.system(size: 12))
                    .foregroundColor(GlassPalette.secondaryLabel)
                Spacer()
            }
        }
    }

    private func summaryHeroCard(_ report: HitsBreakdownReport) -> some View {
        VStack(alignment: .leading, spacing: 7.5) {
            HStack(alignment: .firstTextBaseline, spacing: 7) {
                Text("~\(report.window_saved_formatted)")
                    .font(.system(size: 23, weight: .bold, design: .rounded).monospacedDigit())
                    .foregroundColor(GlassPalette.primaryLabel)
                Text("Tokens 近 24h 净节省")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(GlassPalette.primaryLabel)
                Spacer()
                Text("累计总省 ~\(report.total_saved_formatted)")
                    .font(.system(size: 10.5, weight: .medium, design: .rounded).monospacedDigit())
                    .foregroundColor(GlassPalette.secondaryLabel)
            }

            HStack(spacing: 5.5) {
                statChip("24h 命中 \(report.window_hits) 次")
                statChip("涉及 \(report.distinct_items) 条经验")
                statChip("赋能 \(report.distinct_sessions) 个会话")
                statChip(String(format: "回报 %.1f×", report.roi_multiple))
            }

            if !report.channels.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 5.5) {
                        ForEach(report.channels) { ch in
                            Text("\(ch.label) ×\(ch.count)")
                                .font(.system(size: 9.5, weight: .medium))
                                .foregroundColor(GlassPalette.secondaryLabel)
                                .padding(.horizontal, 7)
                                .padding(.vertical, 2)
                                .background(
                                    Capsule()
                                        .fill(GlassPalette.recessedChannelFill(colorScheme))
                                )
                        }
                    }
                }
            }
        }
        .padding(11.5)
        .liquidDroplet(cornerRadius: 16, emphasized: true, activeRim: true, interactiveHover: false)
    }

    private func statChip(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10, weight: .medium))
            .foregroundColor(GlassPalette.secondaryLabel)
            .padding(.horizontal, 7)
            .padding(.vertical, 2.5)
            .background(
                Capsule()
                    .fill(GlassPalette.microCapsuleFill(colorScheme))
            )
            .overlay(
                Capsule()
                    .strokeBorder(GlassPalette.specularRimGradient(colorScheme), lineWidth: 0.5)
            )
    }
}

struct DashboardPopoverView: View {
    @Environment(\.colorScheme) private var colorScheme
    @ObservedObject var vm: DashboardViewModel

    var body: some View {
        VStack(spacing: 11) {
            if vm.isShowingDetail {
                KnowledgeDetailView(vm: vm)
                    .transition(
                        .asymmetric(
                            insertion: .move(edge: .trailing).combined(with: .opacity),
                            removal: .move(edge: .trailing).combined(with: .opacity)
                        )
                    )
            } else if vm.showHitsBreakdown {
                HitsBreakdownView(vm: vm)
                    .transition(
                        .asymmetric(
                            insertion: .move(edge: .trailing).combined(with: .opacity),
                            removal: .move(edge: .trailing).combined(with: .opacity)
                        )
                    )
            } else {
                headerSection
                if let snap = vm.snapshot {
                    TokenGasGaugeCard(
                        gauge: snap.gas_gauge,
                        action: { vm.openHitsBreakdown() }
                    )
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
                                        SessionCardView(
                                            item: sess,
                                            onSelectKnowledge: { kind, subject, ws, title in
                                                vm.openKnowledgeDetail(
                                                    kind: kind,
                                                    subject: subject,
                                                    workspace: ws,
                                                    fallbackTitle: title
                                                )
                                            }
                                        )
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
                                        ActivityRowView(
                                            item: act,
                                            onSelect: { kind, subject, ws, title in
                                                vm.openKnowledgeDetail(
                                                    kind: kind,
                                                    subject: subject,
                                                    workspace: ws,
                                                    fallbackTitle: title
                                                )
                                            }
                                        )
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
            }

            footerSection
        }
        .padding(15)
        .frame(width: 426, height: 612)
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
                    NooMeshGlyphView(size: 20.5)
                        .foregroundColor(GlassPalette.primaryLabel)
                }

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text("NooMesh")
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
                title: "24h 命中",
                value: "\(snap.totals.today_hits)",
                subtitle: "累计 \(snap.totals.total_hits) 次",
                action: { vm.openHitsBreakdown() }
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
            HStack(spacing: 5.5) {
                LiquidCapsuleButton(
                    title: "门户",
                    systemImage: "safari",
                    disabled: vm.isBusyAction,
                    action: { vm.openPortal() }
                )
                LiquidCapsuleButton(
                    title: "隔空投送",
                    systemImage: "airplayaudio",
                    disabled: vm.isBusyAction,
                    action: { vm.shareBundleViaAirDrop() }
                )
                LiquidCapsuleButton(
                    title: "导入包",
                    systemImage: "square.and.arrow.down",
                    disabled: vm.isBusyAction,
                    action: { vm.pickAndImportBundle() }
                )
                LiquidCapsuleButton(
                    title: "整理",
                    systemImage: "arrow.triangle.2.circlepath",
                    disabled: vm.isBusyAction,
                    action: { vm.runDreamPass() }
                )

                Spacer(minLength: 2)

                Button(action: { vm.refreshNow(allowReorder: true) }) {
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
    private let panelSize = NSSize(width: 426, height: 612)
    private var statusItem: NSStatusItem!
    private var panel: FloatingGlassPanel!
    private var viewModel: DashboardViewModel!
    private var globalClickMonitor: Any?
    private var localKeyMonitor: Any?
    private var suspendAutoDismiss: Bool = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        let cli = RuntimeCLI.parseFromCommandLine()
        viewModel = DashboardViewModel(cli: cli)
        viewModel.onSuspendAutoDismiss = { [weak self] suspended in
            self?.suspendAutoDismiss = suspended
        }

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = makeNooMeshStatusBarImage(pointSize: 18.5)
            button.imagePosition = .imageLeading
            button.font = NSFont.monospacedDigitSystemFont(ofSize: 11.5, weight: .medium)
            button.target = self
            button.action = #selector(togglePanel(_:))
            button.toolTip = "NooMesh · 本地知识实时状态"
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
        button.toolTip = "NooMesh · 累计净省 ~\(snap.gas_gauge.total_saved_formatted) Tokens (24h +\(snap.gas_gauge.today_saved_formatted)) · 24h 命中 \(hits) 次 · \(active) 个会话执行中"
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
        viewModel.resetDetailWithoutAnimation()
        panel.orderOut(nil)
    }

    private func installDismissMonitors() {
        removeDismissMonitors()
        globalClickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown, .otherMouseDown]
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self = self, self.panel.isVisible, !self.suspendAutoDismiss else { return }
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
                    guard let self = self else { return }
                    if self.viewModel.isShowingDetail {
                        self.viewModel.closeKnowledgeDetail()
                    } else if self.viewModel.showHitsBreakdown {
                        self.viewModel.closeHitsBreakdown()
                    } else {
                        self.closePanel()
                    }
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
        guard !suspendAutoDismiss else { return }
        closePanel()
    }

    func application(_ application: NSApplication, open urls: [URL]) {
        guard let firstBundle = urls.first(where: { $0.pathExtension.lowercased() == "noomesh" }) else {
            return
        }
        if !panel.isVisible {
            showPanel()
        }
        viewModel.importBundleFile(at: firstBundle)
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
struct NooMeshMenuBarMain {
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
