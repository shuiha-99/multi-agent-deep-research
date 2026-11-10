import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, ReactNode } from "react";
import {
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  BarChart3,
  BookOpen,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Clock3,
  Database,
  ExternalLink,
  FileBarChart2,
  FileText,
  FlaskConical,
  FolderOpen,
  Globe2,
  Layers3,
  Loader2,
  Network,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Upload,
  X,
  XCircle,
  Zap,
  AlertCircle,
  Workflow,
  PanelLeftClose,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { activeStatus, api, artifactLink, safeLink } from "./api";
import type {
  AppConfig,
  DatasetResource,
  DocumentResource,
  Evidence,
  Health,
  Metrics,
  Mode,
  ResearchEvent,
  Task,
} from "./types";

type View = "workspace" | "library" | "metrics" | "settings";
type Tab = "report" | "activity" | "sources";
const SAMPLE_QUESTION =
  "分析示例店铺近三个月咖啡用品的销售变化，结合产品资料和示例市场资料，给出下月选品与运营建议。请标注数据来源与限制。";
const statusNames: Record<string, string> = {
  queued: "等待执行",
  running: "研究中",
  recovering: "恢复中",
  succeeded: "已完成",
  failed: "执行失败",
  cancel_requested: "取消中",
  cancelled: "已取消",
};
const agentNames: Record<string, string> = {
  orchestrator: "主研究员",
  web: "联网研究",
  knowledge: "知识检索",
  data: "数据分析",
};
const agentActions: Record<string, string> = {
  web: "检索公开资料与核查来源",
  knowledge: "检索产品资料与核对口径",
  data: "计算经营指标并生成图表",
};
const toolNames: Record<string, string> = {
  search_web: "搜索公开信息",
  fetch_page: "读取网页",
  extract_page_tables: "提取网页表格",
  search_knowledge: "检索知识库",
  read_chunk: "核查原文片段",
  get_document_metadata: "读取文档信息",
  inspect_dataset: "检查经营数据",
  aggregate_metrics: "计算经营指标",
  create_chart: "生成分析图表",
};

const percent = (n: number | undefined) => `${Math.round((n ?? 0) * 100)}%`;
const number = (n: number | undefined) => (n ?? 0).toLocaleString("zh-CN");
const date = (s: string, full = false) => {
  const d = new Date(s);
  return Number.isNaN(d.getTime())
    ? s
    : d.toLocaleString(
        "zh-CN",
        full
          ? {
              month: "2-digit",
              day: "2-digit",
              hour: "2-digit",
              minute: "2-digit",
            }
          : { hour: "2-digit", minute: "2-digit", second: "2-digit" },
      );
};
const errorMessage = (e: unknown) =>
  e instanceof Error ? e.message : "操作未完成，请重试";
const agentIcon = (agent: string, size = 17) =>
  agent === "web" ? (
    <Globe2 size={size} />
  ) : agent === "knowledge" ? (
    <BookOpen size={size} />
  ) : agent === "data" ? (
    <BarChart3 size={size} />
  ) : (
    <Network size={size} />
  );

function Badge({
  status,
  children,
}: {
  status?: string;
  children?: ReactNode;
}) {
  return (
    <span className={`badge ${status ?? ""}`}>
      <span />
      {children ?? statusNames[status ?? ""] ?? status}
    </span>
  );
}

function EventDescription({ event }: { event: ResearchEvent }) {
  const d = event.data;
  const message =
    typeof d.message === "string"
      ? d.message
      : typeof d.summary === "string"
        ? d.summary
        : typeof d.error === "string"
          ? d.error
          : null;
  const names: Record<string, string> = {
    task_created: "研究任务已创建",
    task_started: "开始研究",
    task_completed: "研究报告已完成",
    task_succeeded: "研究报告已完成",
    task_failed: "研究执行失败",
    task_cancelled: "研究已取消",
    cancel_requested: "已请求取消",
    task_recovered: "从检查点恢复任务",
    recovery_started: "正在恢复任务",
    plan_updated: "更新研究计划",
    phase: typeof d.phase === "string" ? d.phase : "切换研究阶段",
    agent_started: `${agentNames[event.agent] ?? event.agent}开始执行`,
    agent_completed: `${agentNames[event.agent] ?? event.agent}完成任务`,
    tool_started: `正在${toolNames[event.tool ?? ""] ?? event.tool ?? "调用工具"}`,
    tool_completed: `${toolNames[event.tool ?? ""] ?? event.tool ?? "工具调用"}完成`,
    tool_failed: `${toolNames[event.tool ?? ""] ?? event.tool ?? "工具调用"}失败`,
    model_started: "正在生成研究结果",
    model_completed: "模型调用完成",
    checkpoint_saved: "执行状态已保存",
    cache_hit: "复用有效缓存结果",
    report_saved: "报告已保存",
    task_running: "任务开始执行",
    recovering: "正在恢复执行",
  };
  return (
    <>
      <strong>{names[event.event_type] ?? message ?? event.event_type}</strong>
      {message && message !== names[event.event_type] && <p>{message}</p>}
      {typeof d.duration_ms === "number" && (
        <span className="event-duration">{Math.round(d.duration_ms)} ms</span>
      )}
      {d.cache_status === "semantic_hit" && (
        <span className="mini-tag">语义命中</span>
      )}
    </>
  );
}

function App() {
  const [view, setView] = useState<View>("workspace");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [task, setTask] = useState<Task | null>(null);
  const [events, setEvents] = useState<ResearchEvent[]>([]);
  const [sources, setSources] = useState<Evidence[]>([]);
  const [documents, setDocuments] = useState<DocumentResource[]>([]);
  const [datasets, setDatasets] = useState<DatasetResource[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [tab, setTab] = useState<Tab>("report");
  const [connection, setConnection] = useState<
    "connecting" | "live" | "offline"
  >("connecting");
  const [filter, setFilter] = useState("");
  const [mobileSidebar, setMobileSidebar] = useState(false);
  const lastSeq = useRef(0);
  const selectedRef = useRef<string | null>(null);
  const taskRef = useRef<Task | null>(null);
  const closeCreate = useCallback(() => setShowCreate(false), []);

  const refreshOverview = useCallback(async () => {
    const [h, c, t, docs, ds, m] = await Promise.all([
      api.health(),
      api.config(),
      api.tasks(),
      api.documents(),
      api.datasets(),
      api.metrics(),
    ]);
    setHealth(h);
    setConfig(c);
    setTasks(t);
    setDocuments(docs);
    setDatasets(ds);
    setMetrics(m);
    setError("");
  }, []);

  useEffect(() => {
    let active = true;
    refreshOverview()
      .catch((e) => {
        if (active) setError(`暂时无法连接研究服务：${errorMessage(e)}`);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    const interval = window.setInterval(() => {
      refreshOverview().catch(() => {});
    }, 6000);
    return () => {
      active = false;
      window.clearInterval(interval);
    };
  }, [refreshOverview]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const mergeEvents = useCallback((incoming: ResearchEvent[]) => {
    const valid = incoming.filter(
      (event) =>
        event.task_id === selectedRef.current &&
        Number.isSafeInteger(event.seq) &&
        event.seq > 0,
    );
    if (!valid.length) return;
    lastSeq.current = Math.max(lastSeq.current, ...valid.map((e) => e.seq));
    setEvents((previous) =>
      Array.from(
        new Map(
          [
            ...previous.filter((e) => e.task_id === selectedRef.current),
            ...valid,
          ].map((e) => [e.seq, e]),
        ).values(),
      ).sort((a, b) => a.seq - b.seq),
    );
  }, []);

  useEffect(() => {
    selectedRef.current = selectedId;
    lastSeq.current = 0;
    taskRef.current = null;
    setTask(null);
    setEvents([]);
    setSources([]);
    setTab("report");
    if (!selectedId) return;
    let disposed = false;
    let socket: WebSocket | null = null;
    let retryTimer: number | undefined;
    let retryDelay = 1000;
    const refresh = async () => {
      try {
        const [updated, history, evidence] = await Promise.all([
          api.task(selectedId),
          api.events(selectedId, lastSeq.current),
          api.sources(selectedId),
        ]);
        if (disposed || selectedRef.current !== selectedId) return;
        setTask(updated);
        taskRef.current = updated;
        mergeEvents(history);
        setSources(evidence);
        setTasks((previous) =>
          previous.map((item) => (item.id === updated.id ? updated : item)),
        );
      } catch (e) {
        if (!disposed) setError(errorMessage(e));
      }
    };
    const connect = () => {
      if (disposed) return;
      setConnection("connecting");
      socket = new WebSocket(
        `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/research/tasks/${encodeURIComponent(selectedId)}/stream?after_seq=${lastSeq.current}`,
      );
      socket.onopen = () => {
        if (!disposed) {
          setConnection("live");
          retryDelay = 1000;
          void refresh();
        }
      };
      socket.onmessage = (message) => {
        if (disposed) return;
        try {
          const event = JSON.parse(message.data) as ResearchEvent;
          if (event.task_id === selectedId && typeof event.seq === "number") {
            mergeEvents([event]);
            if (
              [
                "task_completed",
                "task_succeeded",
                "task_failed",
                "task_cancelled",
                "phase",
                "plan_updated",
              ].includes(event.event_type)
            )
              void refresh();
          }
        } catch {
          /* Heartbeat frames do not become activity records. */
        }
      };
      socket.onerror = () => socket?.close();
      socket.onclose = () => {
        if (disposed) return;
        setConnection("offline");
        if (!taskRef.current || activeStatus(taskRef.current.status)) {
          retryTimer = window.setTimeout(connect, retryDelay);
          retryDelay = Math.min(retryDelay * 2, 10000);
        }
      };
    };
    void refresh();
    connect();
    const interval = window.setInterval(() => {
      if (!taskRef.current || activeStatus(taskRef.current.status))
        void refresh();
    }, 2500);
    return () => {
      disposed = true;
      window.clearInterval(interval);
      window.clearTimeout(retryTimer);
      socket?.close();
    };
  }, [selectedId, mergeEvents]);

  const selectTask = (item: Task) => {
    selectedRef.current = item.id;
    setSelectedId(item.id);
    setView("workspace");
    setMobileSidebar(false);
  };
  const runAction = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setError("");
    try {
      await action();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy("");
    }
  };
  const loadSamples = () =>
    runAction("samples", async () => {
      await api.samples();
      await refreshOverview();
      setNotice("示例经营数据与资料已载入，并已标注数据来源。");
    });
  const startSample = () =>
    runAction("sample-task", async () => {
      const loaded = await api.samples();
      const created = await api.create({
        question: SAMPLE_QUESTION,
        title: "咖啡用品经营研究",
        mode: "demo",
        dataset_ids: loaded.datasets.map((d) => d.id),
        document_ids: loaded.documents.map((d) => d.id),
        scope: {},
      });
      await refreshOverview();
      selectTask(created);
    });
  const upload = (
    kind: "documents" | "datasets",
    event: ChangeEvent<HTMLInputElement>,
  ) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = "";
    if (!files.length) return;
    void runAction(`upload-${kind}`, async () => {
      for (const file of files) await api.upload(kind, file);
      await refreshOverview();
      setNotice(`${files.length} 个文件已上传并解析。`);
    });
  };
  const createTask = async (payload: Parameters<typeof api.create>[0]) => {
    const created = await api.create(payload);
    await refreshOverview();
    selectTask(created);
    setShowCreate(false);
  };
  const nav = (target: View) => {
    setView(target);
    setMobileSidebar(false);
  };
  const missing = [...(config?.missing_live_config ?? [])];
  if (config && !config.redis?.available) {
    missing.push("Redis 未连接");
  } else if (config?.redis?.available && !config.redis.vector_search_available) {
    missing.push("Redis 向量检索未就绪");
  }
  const selectedMetrics = task?.metrics;
  const selectedMode = task?.mode ?? tasks.find((item) => item.id === selectedId)?.mode;
  const showOnlineNotice = selectedMode === "live" || (!selectedId && config?.live_ready === true);
  const embeddedChartPaths = new Set(
    Array.from(
      (task?.report_markdown ?? "").matchAll(
        /!\[[^\]]*\]\((\/api\/artifacts\/[a-zA-Z0-9_-]+)\)/g,
      ),
      (match) => match[1],
    ),
  );
  const filteredTasks = tasks.filter((t) =>
    `${t.title} ${t.question}`.toLowerCase().includes(filter.toLowerCase()),
  );

  return (
    <div className="app-shell">
      {mobileSidebar && (
        <button
          className="sidebar-scrim"
          onClick={() => setMobileSidebar(false)}
          aria-label="关闭导航"
        />
      )}
      <aside className={`sidebar ${mobileSidebar ? "is-open" : ""}`}>
        <button
          className="brand"
          onClick={() => {
            nav("workspace");
            setSelectedId(null);
          }}
        >
          <span className="brand-symbol">
            <Network size={24} />
          </span>
          <span>
            研序<span className="brand-en">RESEARCH STUDIO</span>
          </span>
        </button>
        <div className="workspace-label">
          个人研究空间 <ChevronDown size={13} />
        </div>
        <button
          className="sidebar-create"
          onClick={() => {
            setShowCreate(true);
            setMobileSidebar(false);
          }}
        >
          <Plus size={17} />
          新建研究<span>↗</span>
        </button>
        <div className="nav-heading">工作空间</div>
        <nav aria-label="主导航">
          <button
            className={view === "workspace" ? "active" : ""}
            onClick={() => nav("workspace")}
          >
            <Layers3 size={18} />
            研究工作台<span className="nav-count">{tasks.length}</span>
          </button>
          <button
            className={view === "library" ? "active" : ""}
            onClick={() => nav("library")}
          >
            <FolderOpen size={18} />
            资料与数据
          </button>
          <button
            className={view === "metrics" ? "active" : ""}
            onClick={() => nav("metrics")}
          >
            <BarChart3 size={18} />
            运行观察
          </button>
          <button
            className={view === "settings" ? "active" : ""}
            onClick={() => nav("settings")}
          >
            <Settings2 size={18} />
            服务配置
          </button>
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <Workflow size={21} />
            <strong>一个目标，多种视角。</strong>
            <p>
              让信息、知识和数据
              <br />
              共同支撑每一个结论。
            </p>
          </div>
          <button className="service-status" onClick={() => nav("settings")}>
            <span className={`status-dot ${health ? "online" : ""}`} />
            <span>{health ? "本地研究服务已连接" : "等待连接研究服务"}</span>
            <ChevronRight size={14} />
          </button>
          <div className="sidebar-version">
            LOCAL WORKSPACE <span>v{health?.version ?? "0.1.0"}</span>
          </div>
        </div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div className="breadcrumb">
            <button
              className="mobile-menu icon-button"
              onClick={() => setMobileSidebar(true)}
              aria-label="打开导航"
            >
              <PanelLeftClose size={20} />
            </button>
            <span>工作空间</span>
            <ChevronRight size={13} />
            <strong>
              {
                {
                  workspace: "研究工作台",
                  library: "资料与数据",
                  metrics: "运行观察",
                  settings: "服务配置",
                }[view]
              }
            </strong>
          </div>
          <div className="topbar-right">
            <span className="local-pill">
              <ShieldCheck size={13} />
              本地运行
            </span>
            <span className="avatar">研</span>
          </div>
        </header>

        <main>
          {error && (
            <div className="error-banner" role="alert">
              <AlertCircle size={18} />
              <span>{error}</span>
              <button
                className="icon-button"
                onClick={() => setError("")}
                aria-label="关闭错误提示"
              >
                <X size={16} />
              </button>
            </div>
          )}
          <div className="page-heading">
            <div>
              <span className="eyebrow">RESEARCH, WITH EVIDENCE</span>
              <h1>
                {
                  {
                    workspace: "研究工作台",
                    library: "资料与数据",
                    metrics: "运行观察",
                    settings: "服务配置",
                  }[view]
                }
              </h1>
              <p>
                {
                  {
                    workspace: "把复杂问题，推进成有据可查的研究。",
                    library: "整理研究所需的知识资料与经营数据。",
                    metrics: "从实际执行记录中，了解每一次研究。",
                    settings: "查看服务连接状态，按需开启在线研究。",
                  }[view]
                }
              </p>
            </div>
            <button
              className="button primary"
              onClick={() => setShowCreate(true)}
            >
              <Plus size={17} />
              新建研究
            </button>
          </div>

          {view === "workspace" && (
            <>
              <div className="mode-notice">
                {showOnlineNotice ? <Globe2 size={17} /> : <FlaskConical size={17} />}
                <span>
                  <strong>{showOnlineNotice
                    ? selectedMode === "live" ? "当前为在线研究" : "在线研究已可用"
                    : "示例模式可直接体验"}</strong>
                  {showOnlineNotice
                    ? "由真实模型规划，按需调用联网搜索和知识检索；经营数据中的模拟内容仍会单独标注。"
                    : "使用模拟经营数据与离线资料，实际执行检索和统计，不调用真实大模型或实时搜索。"}
                </span>
                <button onClick={() => nav("settings")}>
                  在线配置
                  <ArrowRight size={14} />
                </button>
              </div>
              {loading ? (
                <div className="loading-state">
                  <Loader2 className="spin" size={25} />
                  <span>正在连接研究空间…</span>
                </div>
              ) : (
                <>
                  {!selectedId && (
                    <section className="intro-card">
                      <div className="intro-copy">
                        <span className="section-kicker">
                          <Sparkles size={14} />
                          从一个好问题开始
                        </span>
                        <h2>
                          连接线索，
                          <br />
                          <span>看见问题的全貌。</span>
                        </h2>
                        <p>
                          主研究员规划任务，三个专业助手协同探索。
                          <br />
                          从公开信息到经营数据，每一步都有迹可循。
                        </p>
                        <div className="intro-actions">
                          <button
                            className="button primary"
                            onClick={() => void startSample()}
                            disabled={!!busy || !health}
                          >
                            {busy === "sample-task" ? (
                              <Loader2 className="spin" size={16} />
                            ) : (
                              <Sparkles size={16} />
                            )}
                            体验示例研究
                            <ArrowRight size={15} />
                          </button>
                          <button
                            className="text-button"
                            onClick={() => setShowCreate(true)}
                          >
                            提出我的问题
                            <ChevronRight size={15} />
                          </button>
                        </div>
                      </div>
                      <div className="research-illustration" aria-hidden="true">
                        <div className="illustration-grid" />
                        <div className="orbit orbit-one" />
                        <div className="orbit orbit-two" />
                        <div className="illustration-center">
                          <Network size={30} />
                          <span>研究目标</span>
                        </div>
                        <div className="illustration-node node-web">
                          <Globe2 size={21} />
                          <span>公开信息</span>
                          <small>发现外部线索</small>
                        </div>
                        <div className="illustration-node node-kb">
                          <BookOpen size={21} />
                          <span>领域知识</span>
                          <small>定位可靠依据</small>
                        </div>
                        <div className="illustration-node node-data">
                          <BarChart3 size={21} />
                          <span>经营数据</span>
                          <small>验证关键发现</small>
                        </div>
                        <div className="illustration-label">
                          <span />
                          ORCHESTRATOR + 3 WORKERS
                        </div>
                      </div>
                    </section>
                  )}

                  <section
                    className={`research-section ${selectedId ? "with-detail" : ""}`}
                  >
                    <div className="task-list-panel">
                      <div className="section-title">
                        <h2>
                          研究任务{" "}
                          <span>
                            {tasks.length.toString().padStart(2, "0")}
                          </span>
                        </h2>
                        {selectedId && (
                          <button
                            className="icon-button"
                            title="返回概览"
                            onClick={() => setSelectedId(null)}
                          >
                            <ArrowLeft size={16} />
                          </button>
                        )}
                      </div>
                      {tasks.length > 0 && (
                        <label className="task-search">
                          <Search size={14} />
                          <input
                            value={filter}
                            onChange={(e) => setFilter(e.target.value)}
                            placeholder="查找研究任务"
                          />
                        </label>
                      )}
                      {filteredTasks.length === 0 ? (
                        <div className="empty-tasks">
                          <span className="empty-icon">
                            <FileBarChart2 size={25} />
                          </span>
                          <h3>
                            {filter
                              ? "没有匹配的研究"
                              : "你的第一份研究，从这里开始"}
                          </h3>
                          <p>
                            {filter
                              ? "换一个关键词试试。"
                              : "提出一个问题，或载入示例了解完整研究过程。"}
                          </p>
                          {!filter && (
                            <button
                              className="text-button"
                              onClick={() => setShowCreate(true)}
                            >
                              创建研究
                              <Plus size={15} />
                            </button>
                          )}
                        </div>
                      ) : (
                        <div className="task-list">
                          {filteredTasks.map((item) => (
                            <button
                              key={item.id}
                              className={`task-card ${selectedId === item.id ? "selected" : ""}`}
                              onClick={() => selectTask(item)}
                            >
                              <div className="task-card-top">
                                <span className="task-icon">
                                  <FileBarChart2 size={19} />
                                </span>
                                <Badge status={item.status} />
                              </div>
                              <h3>
                                {item.title || item.question.slice(0, 30)}
                              </h3>
                              <p>{item.question}</p>
                              <div className="task-card-bottom">
                                <span>{date(item.created_at, true)}</span>
                                <span className={`mode-tag ${item.mode}`}>
                                  {item.mode === "demo" ? "示例" : "在线"}
                                </span>
                                <ChevronRight size={14} />
                              </div>
                              {activeStatus(item.status) && (
                                <div className="task-progress">
                                  <span
                                    style={{
                                      width: `${Math.min(100, Math.max(3, item.progress || 0))}%`,
                                    }}
                                  />
                                </div>
                              )}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>

                    {selectedId && (
                      <div className="detail-panel">
                        {!task ? (
                          <div className="loading-state">
                            <Loader2 className="spin" size={24} />
                            正在读取研究…
                          </div>
                        ) : (
                          <>
                            <div className="detail-heading">
                              <div>
                                <div className="detail-title-row">
                                  <Badge status={task.status} />
                                  <span className={`mode-tag ${task.mode}`}>
                                    {task.mode === "demo"
                                      ? "示例研究"
                                      : "在线研究"}
                                  </span>
                                </div>
                                <h2>{task.title || "研究任务"}</h2>
                                <p>{task.question}</p>
                              </div>
                              <div className="detail-actions">
                                {activeStatus(task.status) &&
                                  task.status !== "cancel_requested" && (
                                    <button
                                      className="button subtle small"
                                      onClick={() =>
                                        void runAction("cancel", async () => {
                                          const updated = await api.cancel(
                                            task.id,
                                          );
                                          if (
                                            selectedRef.current === updated.id
                                          ) {
                                            const merged = { ...taskRef.current, ...updated };
                                            setTask(merged);
                                            taskRef.current = merged;
                                          }
                                        })
                                      }
                                      disabled={!!busy}
                                    >
                                      <Square size={13} />
                                      取消
                                    </button>
                                  )}
                                {["failed", "cancelled"].includes(
                                  task.status,
                                ) && (
                                  <button
                                    className="button subtle small"
                                    onClick={() =>
                                      void runAction("retry", async () => {
                                        const next = await api.retry(task.id);
                                        await refreshOverview();
                                        selectTask(next);
                                      })
                                    }
                                    disabled={!!busy}
                                  >
                                    <RefreshCw size={14} />
                                    重新研究
                                  </button>
                                )}
                                {task.report_markdown && (
                                  <a
                                    className="button subtle small"
                                    href={`/api/research/tasks/${encodeURIComponent(task.id)}/report/download`}
                                  >
                                    <ArrowDownToLine size={14} />
                                    下载
                                  </a>
                                )}
                              </div>
                            </div>
                            {task.error && (
                              <div className="task-error">
                                <XCircle size={17} />
                                <span>{task.error}</span>
                              </div>
                            )}
                            <div className="detail-metrics">
                              <div>
                                <span>研究阶段</span>
                                <strong>
                                  {task.phase || statusNames[task.status]}
                                </strong>
                              </div>
                              <div>
                                <span>工具调用</span>
                                <strong>
                                  {number(selectedMetrics?.total_calls)}
                                  <small>次</small>
                                </strong>
                              </div>
                              <div>
                                <span>证据来源</span>
                                <strong>
                                  {sources.length}
                                  <small>条</small>
                                </strong>
                              </div>
                              <div>
                                <span>缓存命中</span>
                                <strong>
                                  {selectedMetrics?.total_calls
                                    ? percent(selectedMetrics.cache_hit_rate)
                                    : "—"}
                                </strong>
                              </div>
                            </div>
                            <div className="detail-content">
                              <div className="detail-main">
                                <div className="tabs" role="tablist">
                                  <button
                                    role="tab"
                                    aria-selected={tab === "report"}
                                    className={tab === "report" ? "active" : ""}
                                    onClick={() => setTab("report")}
                                  >
                                    <FileText size={15} />
                                    研究报告
                                  </button>
                                  <button
                                    role="tab"
                                    aria-selected={tab === "activity"}
                                    className={
                                      tab === "activity" ? "active" : ""
                                    }
                                    onClick={() => setTab("activity")}
                                  >
                                    <Radio size={15} />
                                    执行过程<span>{events.length}</span>
                                  </button>
                                  <button
                                    role="tab"
                                    aria-selected={tab === "sources"}
                                    className={
                                      tab === "sources" ? "active" : ""
                                    }
                                    onClick={() => setTab("sources")}
                                  >
                                    <BookOpen size={15} />
                                    证据来源<span>{sources.length}</span>
                                  </button>
                                </div>
                                <div className="tab-content" role="tabpanel">
                                  {tab === "report" &&
                                    (task.report_markdown ? (
                                      <>
                                        <div className="report-toolbar">
                                          <span>
                                            <CheckCircle2 size={14} />
                                            报告已生成
                                          </span>
                                          <span>
                                            {task.mode === "demo"
                                              ? "模拟数据 · 离线示例资料"
                                              : "请结合来源核查结论"}
                                          </span>
                                        </div>
                                        <article className="markdown">
                                          <ReactMarkdown
                                            remarkPlugins={[remarkGfm]}
                                            components={{
                                              a: ({ href, children }) => {
                                                const local = artifactLink(
                                                  href,
                                                  task.artifacts,
                                                );
                                                if (local)
                                                  return (
                                                    <a
                                                      href={local}
                                                      target="_blank"
                                                      rel="noreferrer"
                                                    >
                                                      {children}
                                                    </a>
                                                  );
                                                const safe = safeLink(href);
                                                return safe ? (
                                                  <a
                                                    href={safe}
                                                    target="_blank"
                                                    rel="noreferrer"
                                                  >
                                                    {children}
                                                    <ExternalLink size={11} />
                                                  </a>
                                                ) : (
                                                  <span>{children}</span>
                                                );
                                              },
                                              img: ({ src, alt }) =>
                                                artifactLink(
                                                  src,
                                                  task.artifacts,
                                                  true,
                                                ) ? (
                                                  <img
                                                    src={artifactLink(
                                                      src,
                                                      task.artifacts,
                                                      true,
                                                    )}
                                                    alt={alt ?? "研究图表"}
                                                    loading="lazy"
                                                  />
                                                ) : (
                                                  <span>{alt}</span>
                                                ),
                                            }}
                                          >
                                            {task.report_markdown}
                                          </ReactMarkdown>
                                        </article>
                                        {task.artifacts
                                          ?.filter(
                                            (a) =>
                                              a.media_type?.startsWith(
                                                "image/",
                                              ) &&
                                              !embeddedChartPaths.has(
                                                `/api/artifacts/${encodeURIComponent(a.id)}`,
                                              ),
                                          )
                                          .map((a) => (
                                            <figure
                                              className="artifact-figure"
                                              key={a.id}
                                            >
                                              <img
                                                src={`/api/artifacts/${encodeURIComponent(a.id)}`}
                                                alt={a.name}
                                              />
                                              <figcaption>{a.name}</figcaption>
                                            </figure>
                                          ))}
                                      </>
                                    ) : (
                                      <div className="report-pending">
                                        <span
                                          className={`pending-symbol ${activeStatus(task.status) ? "is-running" : ""}`}
                                        >
                                          {activeStatus(task.status) ? (
                                            <Network size={30} />
                                          ) : (
                                            <FileText size={30} />
                                          )}
                                        </span>
                                        <h3>
                                          {activeStatus(task.status)
                                            ? "研究正在推进"
                                            : "尚未生成报告"}
                                        </h3>
                                        <p>
                                          {activeStatus(task.status)
                                            ? "助手正在收集证据与分析数据。完成后，报告会自动出现在这里。"
                                            : "查看执行过程，了解任务结束的具体原因。"}
                                        </p>
                                        <button
                                          className="text-button"
                                          onClick={() => setTab("activity")}
                                        >
                                          查看执行过程
                                          <ArrowRight size={15} />
                                        </button>
                                        {activeStatus(task.status) && (
                                          <div className="report-progress">
                                            <span
                                              style={{
                                                width: `${Math.min(100, Math.max(3, task.progress || 0))}%`,
                                              }}
                                            />
                                          </div>
                                        )}
                                      </div>
                                    ))}
                                  {tab === "activity" && (
                                    <>
                                      <div className="activity-heading">
                                        <span>实际执行记录</span>
                                        <span
                                          className={`connection ${connection}`}
                                        >
                                          <span />
                                          {connection === "live"
                                            ? "实时连接"
                                            : connection === "connecting"
                                              ? "正在连接"
                                              : activeStatus(task.status)
                                                ? "自动重连 · 状态同步中"
                                                : "任务已结束"}
                                        </span>
                                      </div>
                                      {events.length === 0 ? (
                                        <EmptyState
                                          icon={<Clock3 size={24} />}
                                          title="等待执行事件"
                                          description="任务开始后，计划、工具调用和结果会记录在这里。"
                                        />
                                      ) : (
                                        <ol className="timeline">
                                          {events.map((event) => (
                                            <li
                                              key={`${event.task_id}-${event.seq}`}
                                              className={
                                                event.event_type.includes(
                                                  "failed",
                                                )
                                                  ? "event-failed"
                                                  : ""
                                              }
                                            >
                                              <span className="timeline-dot">
                                                {event.event_type.includes(
                                                  "failed",
                                                ) ? (
                                                  <X size={10} />
                                                ) : event.event_type.includes(
                                                    "completed",
                                                  ) ||
                                                  event.event_type ===
                                                    "checkpoint_saved" ? (
                                                  <Check size={10} />
                                                ) : (
                                                  <Circle size={7} />
                                                )}
                                              </span>
                                              <div className="timeline-body">
                                                <div className="event-meta">
                                                  <span>
                                                    {agentNames[event.agent] ??
                                                      event.agent}
                                                  </span>
                                                  <time>
                                                    {date(event.created_at)}
                                                  </time>
                                                </div>
                                                <EventDescription
                                                  event={event}
                                                />
                                              </div>
                                            </li>
                                          ))}
                                        </ol>
                                      )}
                                    </>
                                  )}
                                  {tab === "sources" && (
                                    <Sources sources={sources} />
                                  )}
                                </div>
                              </div>
                              <aside className="research-aside">
                                <div className="aside-heading">
                                  <Workflow size={16} />
                                  <h3>研究计划</h3>
                                  <span>
                                    {task.plan?.filter((p) =>
                                      [
                                        "completed",
                                        "succeeded",
                                        "done",
                                      ].includes(p.status),
                                    ).length ?? 0}
                                    /{task.plan?.length ?? 0}
                                  </span>
                                </div>
                                {task.plan?.length ? (
                                  <div className="plan-list">
                                    {task.plan.map((item, index) => (
                                      <div
                                        key={item.id}
                                        className={`plan-item ${item.status}`}
                                      >
                                        <span className="plan-index">
                                          {[
                                            "completed",
                                            "succeeded",
                                            "done",
                                          ].includes(item.status) ? (
                                            <Check size={13} />
                                          ) : item.status === "running" ? (
                                            <Loader2
                                              className="spin"
                                              size={13}
                                            />
                                          ) : (
                                            String(index + 1).padStart(2, "0")
                                          )}
                                        </span>
                                        <div>
                                          <span className="plan-agent">
                                            {agentIcon(item.agent, 12)}
                                            {agentNames[item.agent] ??
                                              item.agent}
                                          </span>
                                          <p>{agentActions[item.agent] ?? "整理研究资料与证据"}</p>
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                ) : (
                                  <p className="aside-empty">
                                    主研究员将在执行时生成计划。
                                  </p>
                                )}
                                <div className="aside-divider" />
                                <h3 className="aside-section-title">
                                  专业助手
                                </h3>
                                <div className="agent-list">
                                  {["web", "knowledge", "data"].map((agent) => {
                                    const plans =
                                      task.plan?.filter(
                                        (p) => p.agent === agent,
                                      ) ?? [];
                                    const running = plans.some(
                                      (p) => p.status === "running",
                                    );
                                    const done =
                                      plans.length > 0 &&
                                      plans.every((p) =>
                                        [
                                          "completed",
                                          "succeeded",
                                          "done",
                                        ].includes(p.status),
                                      );
                                    return (
                                      <div className="agent-item" key={agent}>
                                        <span
                                          className={`agent-avatar ${agent}`}
                                        >
                                          {agentIcon(agent)}
                                        </span>
                                        <div>
                                          <strong>{agentNames[agent]}</strong>
                                          <small>
                                            {agent === "web"
                                              ? "公开信息与市场线索"
                                              : agent === "knowledge"
                                                ? "文档检索与原文核查"
                                                : "指标计算与图表生成"}
                                          </small>
                                        </div>
                                        <span
                                          className={`agent-state ${running ? "running" : done ? "done" : ""}`}
                                        >
                                          {running
                                            ? "执行中"
                                            : done
                                              ? "完成"
                                              : plans.length
                                                ? "待执行"
                                                : "未调用"}
                                        </span>
                                      </div>
                                    );
                                  })}
                                </div>
                                <div className="aside-divider" />
                                <div className="task-info">
                                  <span>
                                    创建时间
                                    <strong>
                                      {date(task.created_at, true)}
                                    </strong>
                                  </span>
                                  <span>
                                    执行轮次<strong>{task.attempt || 1}</strong>
                                  </span>
                                  <span>
                                    关联资料
                                    <strong>
                                      {task.document_ids.length} 份文档 ·{" "}
                                      {task.dataset_ids.length} 份数据
                                    </strong>
                                  </span>
                                </div>
                              </aside>
                            </div>
                          </>
                        )}
                      </div>
                    )}
                  </section>
                  {!selectedId && (
                    <div className="workflow-strip">
                      <span>研究如何进行</span>
                      {[
                        {
                          icon: <Network size={18} />,
                          title: "01 规划问题",
                          description: "分解目标与证据需求",
                        },
                        {
                          icon: <Globe2 size={18} />,
                          title: "02 检索与分析",
                          description: "专业助手调用实际工具",
                        },
                        {
                          icon: <ShieldCheck size={18} />,
                          title: "03 核查依据",
                          description: "保留来源与计算口径",
                        },
                        {
                          icon: <FileText size={18} />,
                          title: "04 形成报告",
                          description: "呈现发现与研究限制",
                        },
                      ].map((step) => (
                        <div key={step.title}>
                          <span>{step.icon}</span>
                          <section>
                            <strong>{step.title}</strong>
                            <small>{step.description}</small>
                          </section>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </>
          )}

          {view === "library" && (
            <>
              <div className="library-toolbar">
                <span>
                  <ShieldCheck size={16} />
                  上传的资料保存在本地，创建研究时选择使用范围。
                </span>
                <button
                  className="button subtle"
                  onClick={() => void loadSamples()}
                  disabled={!!busy || !health}
                >
                  {busy === "samples" ? (
                    <Loader2 className="spin" size={16} />
                  ) : (
                    <FlaskConical size={16} />
                  )}
                  加载示例资料
                </button>
              </div>
              <div className="library-grid">
                <section className="resource-panel">
                  <div className="resource-heading">
                    <div>
                      <span className="resource-icon">
                        <BookOpen size={21} />
                      </span>
                      <h2>
                        知识资料<span>{documents.length} 份</span>
                      </h2>
                    </div>
                    <label
                      className={`button subtle small upload-label ${busy ? "disabled" : ""}`}
                    >
                      <Upload size={14} />
                      上传资料
                      <input
                        type="file"
                        accept=".txt,.md,.pdf"
                        multiple
                        disabled={!!busy}
                        onChange={(e) => upload("documents", e)}
                      />
                    </label>
                  </div>
                  <p className="resource-description">
                    产品说明、研究资料与业务文档 · TXT / MD / PDF
                  </p>
                  {documents.length ? (
                    <div className="resource-list">
                      {documents.map((doc) => (
                        <div className="resource-row" key={doc.id}>
                          <span className="file-symbol">
                            <FileText size={21} />
                          </span>
                          <div>
                            <strong>{doc.name}</strong>
                            <p>
                              {doc.chunk_count} 个检索片段 ·{" "}
                              {date(doc.created_at, true)}
                              {doc.metadata?.sample ? " · 示例资料" : ""}
                            </p>
                          </div>
                          <CheckCircle2 size={16} className="ready-icon" />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <EmptyState
                      icon={<BookOpen size={26} />}
                      title="还没有知识资料"
                      description="上传文档，让研究建立在你的业务知识之上。"
                    />
                  )}
                </section>
                <section className="resource-panel">
                  <div className="resource-heading">
                    <div>
                      <span className="resource-icon data">
                        <Database size={21} />
                      </span>
                      <h2>
                        经营数据<span>{datasets.length} 份</span>
                      </h2>
                    </div>
                    <label
                      className={`button subtle small upload-label ${busy ? "disabled" : ""}`}
                    >
                      <Upload size={14} />
                      上传 CSV
                      <input
                        type="file"
                        accept=".csv"
                        multiple
                        disabled={!!busy}
                        onChange={(e) => upload("datasets", e)}
                      />
                    </label>
                  </div>
                  <p className="resource-description">
                    订单、销售与商品数据 · CSV
                  </p>
                  {datasets.length ? (
                    <div className="resource-list">
                      {datasets.map((ds) => (
                        <div className="resource-row" key={ds.id}>
                          <span className="file-symbol data">
                            <Database size={21} />
                          </span>
                          <div>
                            <strong>{ds.name}</strong>
                            <p>
                              {number(ds.row_count)} 行 · {ds.columns.length}{" "}
                              个字段{ds.metadata?.sample ? " · 模拟数据" : ""}
                            </p>
                            <div className="column-tags">
                              {ds.columns.slice(0, 5).map((col) => (
                                <span key={col}>{col}</span>
                              ))}
                              {ds.columns.length > 5 && (
                                <span>+{ds.columns.length - 5}</span>
                              )}
                            </div>
                          </div>
                          <CheckCircle2 size={16} className="ready-icon" />
                        </div>
                      ))}
                    </div>
                  ) : (
                    <EmptyState
                      icon={<Database size={26} />}
                      title="还没有经营数据"
                      description="上传 CSV，使用真实计算核对经营变化。"
                    />
                  )}
                </section>
              </div>
              <div className="library-footnote">
                <AlertCircle size={16} />
                示例文件会明确标注模拟属性。研究使用的资料以创建任务时的选择为准。
              </div>
            </>
          )}

          {view === "metrics" && <MetricsView metrics={metrics} />}

          {view === "settings" && (
            <>
              <section className="settings-intro">
                <span className="settings-illustration">
                  <Settings2 size={28} />
                </span>
                <div>
                  <h2>先验证流程，再连接真实服务。</h2>
                  <p>
                    示例模式无需模型密钥。在线模式使用本地环境文件中的模型、搜索与向量服务配置。
                  </p>
                </div>
                <Badge status={config?.live_ready ? "succeeded" : "queued"}>
                  {config?.live_ready ? "在线配置已就绪" : "在线配置待完善"}
                </Badge>
              </section>
              <div className="service-grid">
                {[
                  {
                    key: "model",
                    title: "研究模型",
                    icon: <Sparkles size={22} />,
                    description: "支持工具调用的兼容模型接口",
                    available: health?.services.model?.configured,
                  },
                  {
                    key: "search",
                    title: "搜索服务",
                    icon: <Globe2 size={22} />,
                    description: "在线检索与公开信息获取",
                    available: health?.services.search?.configured,
                  },
                  {
                    key: "embedding",
                    title: "向量模型",
                    icon: <Network size={22} />,
                    description: "知识检索与余弦语义匹配",
                    available: health?.services.embedding?.configured,
                  },
                  {
                    key: "redis",
                    title: "Redis",
                    icon: <Database size={22} />,
                    description: config?.redis?.note ?? "任务运行元数据与语义缓存",
                    available: config?.redis?.available && config.redis.vector_search_available,
                    statusLabel: config?.redis?.available
                      ? config.redis.vector_search_available ? "已就绪" : "缺少向量能力"
                      : "未连接",
                  },
                ].map((service) => (
                  <div className="service-card" key={service.key}>
                    <div>
                      <span>{service.icon}</span>
                      <Badge
                        status={service.available ? "succeeded" : "queued"}
                      >
                        {service.statusLabel ?? (service.available ? "已配置" : "未连接")}
                      </Badge>
                    </div>
                    <h3>{service.title}</h3>
                    <p>{service.description}</p>
                  </div>
                ))}
              </div>
              <section className="config-panel">
                <h2>
                  <ShieldCheck size={19} />
                  本地配置说明
                </h2>
                <p>
                  在项目根目录复制 <code>.env.example</code> 为{" "}
                  <code>.env</code>
                  ，填写服务地址、模型名称与密钥，然后重启后端服务。页面只显示配置状态，不读取或展示密钥。
                </p>
                {missing.length > 0 && (
                  <div className="missing-config">
                    <strong>在线研究还需配置</strong>
                    <div>
                      {missing.map((item) => (
                        <span key={item}>{item}</span>
                      ))}
                    </div>
                  </div>
                )}
                <div className="config-note">
                  <FlaskConical size={17} />
                  <span>
                    示例模式使用离线材料与模拟经营数据。示例缓存或执行耗时不能作为在线系统的性能成绩。
                  </span>
                </div>
              </section>
            </>
          )}
          <footer className="page-footer">
            <span>研序 · 多智能体深度研究系统</span>
            <span>每个结论，都应有可追溯的依据。</span>
          </footer>
        </main>
      </div>

      {showCreate && (
        <CreateModal
          documents={documents}
          datasets={datasets}
          missing={missing}
          onClose={closeCreate}
          onCreate={createTask}
          onLoadSamples={loadSamples}
        />
      )}
      {notice && (
        <div className="toast" role="status">
          <CheckCircle2 size={18} />
          {notice}
          <button onClick={() => setNotice("")} aria-label="关闭通知">
            <X size={15} />
          </button>
        </div>
      )}
    </div>
  );
}

function EmptyState({
  icon,
  title,
  description,
}: {
  icon: ReactNode;
  title: string;
  description: string;
}) {
  return (
    <div className="empty-state">
      <span>{icon}</span>
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}

function Sources({ sources }: { sources: Evidence[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (!sources.length)
    return (
      <EmptyState
        icon={<BookOpen size={26} />}
        title="尚未收集到证据"
        description="网页来源、文档片段与数据计算依据会在研究执行后显示。"
      />
    );
  return (
    <div className="source-list">
      <p className="source-intro">
        以下内容来自本次任务实际保存的来源。来源可定位，不代表结论已经过人工审核。
      </p>
      {sources.map((source, index) => {
        const url = safeLink(source.url);
        const metadata = source.metadata ?? {};
        return (
          <div className="source-card" key={source.id}>
            <div className="source-card-heading">
              <span className="source-number">
                {String(index + 1).padStart(2, "0")}
              </span>
              <div>
                <h3>{source.title}</h3>
                <div className="source-meta">
                  <span>
                    {(
                      {
                        web: "网页资料",
                        knowledge: "知识文档",
                        document: "知识文档",
                        data: "数据计算",
                        dataset: "经营数据",
                        demo: "离线示例",
                      } as Record<string, string>
                    )[source.source_type] ?? source.source_type}
                  </span>
                  <span>{date(source.created_at, true)}</span>
                  {metadata.sample === true && <span>示例来源</span>}
                </div>
              </div>
              {url && (
                <a
                  className="icon-button"
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  title="打开原始来源"
                >
                  <ExternalLink size={16} />
                </a>
              )}
            </div>
            <p
              className={
                expanded === source.id ? "source-text expanded" : "source-text"
              }
            >
              {source.content}
            </p>
            <div className="source-card-footer">
              <code>{source.id}</code>
              {source.content.length > 180 && (
                <button
                  onClick={() =>
                    setExpanded(expanded === source.id ? null : source.id)
                  }
                >
                  {expanded === source.id ? "收起原文" : "展开原文"}
                  <ChevronDown size={13} />
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function MetricsView({ metrics }: { metrics: Metrics | null }) {
  const hasCalls = !!metrics?.total_calls;
  return (
    <>
      <div className="metrics-grid">
        {[
          {
            icon: <Workflow size={19} />,
            label: "工具调用",
            value: number(metrics?.total_calls),
            unit: "次",
            detail: "来自实际工具执行记录",
          },
          {
            icon: <CheckCircle2 size={19} />,
            label: "调用成功率",
            value: hasCalls ? percent(metrics?.success_rate) : "—",
            unit: "",
            detail: "与工具调用总量对应",
          },
          {
            icon: <Clock3 size={19} />,
            label: "平均调用耗时",
            value: hasCalls
              ? Math.round(metrics?.average_duration_ms ?? 0).toLocaleString()
              : "—",
            unit: "ms",
            detail: "包含实际记录的调用路径",
          },
          {
            icon: <Zap size={19} />,
            label: "缓存命中率",
            value: hasCalls ? percent(metrics?.cache_hit_rate) : "—",
            unit: "",
            detail: `${number(metrics?.exact_hits)} 次精确 · ${number(metrics?.semantic_hits)} 次语义`,
          },
        ].map((item) => (
          <section className="metric-card" key={item.label}>
            <div>
              <span>{item.label}</span>
              {item.icon}
            </div>
            <strong>
              {item.value}
              <small>{item.unit}</small>
            </strong>
            <p>{item.detail}</p>
          </section>
        ))}
      </div>
      <div className="metrics-note">
        <AlertCircle size={16} />
        这里展示本机实际记录，可能同时包含示例与在线任务。截图中的性能目标需通过独立评测验证。
      </div>
      <section className="metrics-table-panel">
        <div className="section-title">
          <h2>工具执行明细</h2>
          <span className="muted">Logger · Monitor · Metrics</span>
        </div>
        {metrics?.by_tool?.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>工具</th>
                  <th>调用次数</th>
                  <th>成功率</th>
                  <th>平均耗时</th>
                </tr>
              </thead>
              <tbody>
                {metrics.by_tool.map((tool) => (
                  <tr key={tool.tool}>
                    <td>
                      <strong>{toolNames[tool.tool] ?? tool.tool}</strong>
                      <code>{tool.tool}</code>
                    </td>
                    <td>{number(tool.calls)}</td>
                    <td>
                      <span className="table-rate">
                        {percent(tool.success_rate)}
                      </span>
                    </td>
                    <td>
                      {Math.round(tool.average_duration_ms).toLocaleString()} ms
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            icon={<BarChart3 size={28} />}
            title="等待第一份执行记录"
            description="完成一次研究后，在这里查看工具调用频次、耗时与成功率。"
          />
        )}
      </section>
    </>
  );
}

function CreateModal({
  documents,
  datasets,
  missing,
  onClose,
  onCreate,
  onLoadSamples,
}: {
  documents: DocumentResource[];
  datasets: DatasetResource[];
  missing: string[];
  onClose: () => void;
  onCreate: (payload: Parameters<typeof api.create>[0]) => Promise<void>;
  onLoadSamples: () => Promise<void>;
}) {
  const [question, setQuestion] = useState("");
  const [mode, setMode] = useState<Mode>("demo");
  const [docIds, setDocIds] = useState<string[]>([]);
  const [datasetIds, setDatasetIds] = useState<string[]>([]);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [region, setRegion] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const modalRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    document.body.style.overflow = "hidden";
    modalRef.current?.querySelector<HTMLTextAreaElement>("textarea")?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !submitting) onClose();
      if (event.key === "Tab" && modalRef.current) {
        const focusables = modalRef.current.querySelectorAll<HTMLElement>(
          "button:not(:disabled),input:not(:disabled),textarea,select,a[href]",
        );
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", keydown);
    return () => {
      document.body.style.overflow = "";
      document.removeEventListener("keydown", keydown);
      previousFocus?.focus();
    };
  }, [onClose, submitting]);
  const toggle = (
    value: string,
    ids: string[],
    setter: (v: string[]) => void,
  ) =>
    setter(
      ids.includes(value) ? ids.filter((id) => id !== value) : [...ids, value],
    );
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!question.trim()) {
      setError("请先描述你的研究问题。");
      return;
    }
    if (startDate && endDate && startDate > endDate) {
      setError("结束日期不能早于开始日期。");
      return;
    }
    if (mode === "live" && missing.length) {
      setError("在线服务配置尚未完成，可以先使用示例模式。");
      return;
    }
    setSubmitting(true);
    try {
      await onCreate({
        question: question.trim(),
        mode,
        dataset_ids: datasetIds,
        document_ids: docIds,
        scope: {
          ...(startDate ? { start_date: startDate } : {}),
          ...(endDate ? { end_date: endDate } : {}),
          ...(region ? { region } : {}),
        },
      });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !submitting) onClose();
      }}
    >
      <div
        className="create-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-title"
        ref={modalRef}
      >
        <div className="modal-heading">
          <div>
            <span className="section-kicker">NEW RESEARCH</span>
            <h2 id="create-title">你想研究什么？</h2>
            <p>明确问题与范围，让每一步探索更有方向。</p>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            disabled={submitting}
            aria-label="关闭新建研究"
          >
            <X size={21} />
          </button>
        </div>
        <form onSubmit={(e) => void submit(e)}>
          <label className="form-label" htmlFor="research-question">
            研究问题<span>必填</span>
          </label>
          <textarea
            id="research-question"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="例如：分析近三个月的销售变化，结合产品资料，给出下月选品建议。"
            maxLength={6000}
            rows={4}
            required
          />
          <button
            type="button"
            className="example-fill"
            onClick={() => setQuestion(SAMPLE_QUESTION)}
          >
            <Sparkles size={13} />
            使用示例问题
          </button>
          <div className="form-label mode-label">运行模式</div>
          <div className="mode-selector">
            <button
              type="button"
              className={mode === "demo" ? "selected" : ""}
              onClick={() => setMode("demo")}
            >
              <FlaskConical size={18} />
              <div>
                <strong>示例模式</strong>
                <span>本地计算 · 离线资料 · 无模型调用</span>
              </div>
              {mode === "demo" && <CheckCircle2 size={17} />}
            </button>
            <button
              type="button"
              className={mode === "live" ? "selected" : ""}
              onClick={() => setMode("live")}
            >
              <Globe2 size={18} />
              <div>
                <strong>在线研究</strong>
                <span>真实模型 · 在线检索 · 需配置服务</span>
              </div>
              {mode === "live" && <CheckCircle2 size={17} />}
            </button>
          </div>
          {mode === "live" && missing.length > 0 && (
            <div className="inline-warning">
              <AlertCircle size={15} />
              <span>尚需配置：{missing.join("、")}</span>
            </div>
          )}
          <div className="attachment-header">
            <span className="form-label">
              关联资料<span>按需选择</span>
            </span>
            <button
              type="button"
              className="text-button"
              onClick={() => void onLoadSamples()}
            >
              加载示例资料
              <Plus size={13} />
            </button>
          </div>
          <div className="attachment-picker">
            <div>
              <h4>
                <BookOpen size={14} />
                知识文档
              </h4>
              {documents.length ? (
                documents.map((doc) => (
                  <label key={doc.id}>
                    <input
                      type="checkbox"
                      checked={docIds.includes(doc.id)}
                      onChange={() => toggle(doc.id, docIds, setDocIds)}
                    />
                    <span>{doc.name}</span>
                  </label>
                ))
              ) : (
                <p>暂无资料，可先加载示例或到资料库上传。</p>
              )}
            </div>
            <div>
              <h4>
                <Database size={14} />
                经营数据
              </h4>
              {datasets.length ? (
                datasets.map((ds) => (
                  <label key={ds.id}>
                    <input
                      type="checkbox"
                      checked={datasetIds.includes(ds.id)}
                      onChange={() => toggle(ds.id, datasetIds, setDatasetIds)}
                    />
                    <span>{ds.name}</span>
                    <small>{number(ds.row_count)} 行</small>
                  </label>
                ))
              ) : (
                <p>暂无数据，公开资料研究可以不选择。</p>
              )}
            </div>
          </div>
          <div className="scope-fields">
            <label>
              开始日期
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
              />
            </label>
            <label>
              结束日期
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
              />
            </label>
            <label>
              地域范围
              <input
                value={region}
                onChange={(e) => setRegion(e.target.value)}
                placeholder="选填，如：中国"
                maxLength={80}
              />
            </label>
          </div>
          {error && (
            <div className="inline-warning" role="alert">
              <AlertCircle size={16} />
              <span>{error}</span>
            </div>
          )}
          <div className="modal-footer">
            <span>
              <ShieldCheck size={14} />
              结果保存在本地研究空间
            </span>
            <button
              className="button primary"
              type="submit"
              disabled={submitting || (mode === "live" && missing.length > 0)}
            >
              {submitting ? (
                <Loader2 className="spin" size={16} />
              ) : (
                <ArrowRight size={16} />
              )}
              {submitting ? "正在创建…" : "开始研究"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default App;
