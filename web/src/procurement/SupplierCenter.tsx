import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive,
  Building2,
  CheckCircle2,
  LoaderCircle,
  Pencil,
  Plus,
  Search,
  Trash2,
  UserRound,
  Users,
} from "lucide-react";
import { useDeferredValue, useMemo, useRef, useState } from "react";

import { procurementApi } from "./api";
import {
  Button,
  CenterPage,
  EmptyState,
  ErrorState,
  Drawer,
  Fact,
  IconButton,
  Modal,
  PageHeader,
  StatusPill,
  dashIfEmpty,
  DASH,
} from "../components/ui";
import type {
  SupplierProfile,
  SupplierSaveRequest,
  SupplierStatus,
  SupplierView,
} from "./types";

const STATUS_OPTIONS: Array<{ value: SupplierStatus | ""; label: string }> = [
  { value: "", label: "全部状态" },
  { value: "ACTIVE", label: "合作中" },
  { value: "PAUSED", label: "已暂停" },
  { value: "BLACKLISTED", label: "黑名单" },
];

const EMPTY_ITEMS: SupplierView[] = [];

function scoreTone(level: string) {
  if (level === "黑名单") return "danger";
  if (level === "优质供应商") return "success";
  if (level === "良好") return "info";
  if (level === "一般") return "neutral";
  return "warning";
}

/** 评分语义化：分数 + 等级 + 一句人话说明（痛点⑨），杜绝"20.0 待落实"式费解组合。 */
function scoreExplanation(supplier: { performance: { score: number | string; level: string }; quote_count: number | string; win_count: number | string }) {
  const score = Number(supplier.performance.score);
  if (supplier.performance.level === "黑名单") return "因列入黑名单被封顶 30 分，禁止新订单";
  if (Number(supplier.quote_count) === 0) return "暂无报价记录，分数仅供参考";
  if (Number(supplier.win_count) === 0) return "参与过报价但尚未中标，中标率得分未形成";
  if (score >= 80) return "中标率与活跃度俱佳，可优先邀标";
  if (score >= 60) return "合作稳定，履约表现良好";
  if (score >= 40) return "表现一般，建议关注交付质量";
  return "分数偏低（多为报价少或未中标），继续合作前先核实履约";
}

const COOPERATION_TONES: Record<string, string> = {
  合作中: "success",
  已暂停: "warning",
  黑名单: "danger",
};

const EMPTY_FORM: SupplierSaveRequest = {
  name: "",
  contact_person: "",
  phone: "",
  email: "",
  address: "",
  main_categories: "",
  status: "ACTIVE",
  notes: "",
};

type Props = {
  onOpenTask: (taskId: string) => void;
};

export function SupplierCenter({ onOpenTask }: Props) {
  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<SupplierStatus | "">("");
  const [page, setPage] = useState(0);
  const [editing, setEditing] = useState<SupplierView | "new" | null>(null);
  const [form, setForm] = useState<SupplierSaveRequest>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [formBusy, setFormBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<SupplierView | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [profileId, setProfileId] = useState<string | null>(null);
  const formDialogRef = useRef<HTMLElement | null>(null);
  const formNameRef = useRef<HTMLInputElement | null>(null);
  const deleteDialogRef = useRef<HTMLElement | null>(null);
  const deleteCancelRef = useRef<HTMLButtonElement | null>(null);
  const drawerRef = useRef<HTMLElement | null>(null);

  const pageSize = 50;
  // 服务端搜索防抖：输入保持即时回显，请求跟随低优先级渲染后的值，
  // 连续击键不再逐字符打接口。
  const deferredSearch = useDeferredValue(search.trim());
  const suppliersQuery = useQuery({
    queryKey: ["procurement-suppliers", deferredSearch, status, page],
    queryFn: () => procurementApi.suppliers(deferredSearch || undefined, status || undefined, page, pageSize),
  });
  const profileQuery = useQuery({
    queryKey: ["procurement-supplier-profile", profileId],
    queryFn: () => procurementApi.supplierProfile(profileId!),
    enabled: !!profileId,
  });
  const suppliers = suppliersQuery.data?.items ?? EMPTY_ITEMS;
  const total = suppliersQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["procurement-suppliers"] }),
      queryClient.invalidateQueries({ queryKey: ["procurement-supplier-profile"] }),
    ]);

  const createMutation = useMutation({
    mutationFn: (input: SupplierSaveRequest) => procurementApi.createSupplier(input),
    onSuccess: async () => {
      await invalidate();
      setEditing(null);
    },
  });
  const updateMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: SupplierSaveRequest }) =>
      procurementApi.updateSupplier(id, input),
    onSuccess: async () => {
      await invalidate();
      setEditing(null);
    },
  });
  const deleteMutation = useMutation({
    mutationFn: (id: string) => procurementApi.deleteSupplier(id),
    onSuccess: async () => {
      await invalidate();
      setDeleteTarget(null);
      if (profileId) setProfileId(null);
    },
  });

  const openCreate = () => {
    setForm(EMPTY_FORM);
    setFormError(null);
    setEditing("new");
  };

  const openEdit = (supplier: SupplierView) => {
    setForm({
      name: supplier.name,
      contact_person: supplier.contact_person || "",
      phone: supplier.phone || "",
      email: supplier.email || "",
      address: supplier.address || "",
      main_categories: supplier.main_categories || "",
      status: supplier.status,
      notes: supplier.notes || "",
    });
    setFormError(null);
    setEditing(supplier);
  };

  const save = async () => {
    if (!form.name?.trim()) {
      setFormError("供应商名称不能为空");
      return;
    }
    setFormBusy(true);
    setFormError(null);
    try {
      if (editing === "new") {
        await createMutation.mutateAsync(form);
      } else if (editing) {
        const updatable: SupplierSaveRequest = {
          contact_person: form.contact_person,
          phone: form.phone,
          email: form.email,
          address: form.address,
          main_categories: form.main_categories,
          status: form.status,
          notes: form.notes,
        };
        await updateMutation.mutateAsync({ id: editing.id, input: updatable });
      }
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error));
    } finally {
      setFormBusy(false);
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await deleteMutation.mutateAsync(deleteTarget.id);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : String(error));
    } finally {
      setDeleteBusy(false);
    }
  };

  const profile = profileQuery.data || null;
  const selectedSupplier = useMemo(
    () => suppliers.find((item) => item.id === profileId) || null,
    [profileId, suppliers]
  );

  return (
    <CenterPage
      header={
        <PageHeader
          icon={<Building2 size={18} />}
          title="供应商管理"
          subtitle="档案与绩效评分（中标率 / 活跃度 / 合作状态派生）"
          aside={<Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>新建供应商</Button>}
        />
      }
      toolbar={
        <div className="proc-action-bar">
          <label className="proc-search">
            <Search size={15} />
            <input
              aria-label="搜索供应商"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setPage(0);
              }}
              placeholder="名称、联系人或主营品类"
            />
          </label>
          <select
            aria-label="供应商状态筛选"
            className="proc-select"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as SupplierStatus | "");
              setPage(0);
            }}
          >
            {STATUS_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>
      }
    >
      <div className="proc-supplier-list" aria-busy={suppliersQuery.isPending}>
        {suppliersQuery.isPending ? (
          <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在加载供应商档案…</div>
        ) : null}
        {suppliersQuery.isError ? (
          <ErrorState
            title="供应商档案加载失败"
            detail={suppliersQuery.error instanceof Error ? suppliersQuery.error.message : "未知错误"}
            onRetry={() => void suppliersQuery.refetch()}
          />
        ) : null}
        {!suppliersQuery.isPending && !suppliersQuery.isError && !suppliers.length ? (
          <EmptyState
            icon={<Archive size={26} />}
            title={search || status ? "没有匹配的供应商" : "还没有供应商档案"}
            hint={search || status ? "调整搜索条件或状态筛选后重试。" : "新建供应商档案后，报价与中标记录将按名称自动关联。"}
            action={search || status ? undefined : <Button variant="primary" icon={<Plus size={15} />} onClick={openCreate}>新建供应商</Button>}
          />
        ) : null}
        {suppliers.map((supplier) => (
          <article className="proc-supplier-card" key={supplier.id}>
            <button
              type="button"
              className="proc-supplier-card-main"
              onClick={() => setProfileId(supplier.id)}
              aria-label={`查看供应商档案 ${supplier.name}`}
            >
              <span className="proc-supplier-avatar" aria-hidden><Building2 size={19} /></span>
              <span className="proc-supplier-info">
                <strong>{supplier.name}</strong>
                <small>{dashIfEmpty(supplier.main_categories)}</small>
              </span>
              <span className="proc-supplier-stats">
                <StatusPill tone={COOPERATION_TONES[supplier.cooperation_status] || "neutral"} size="compact">{supplier.cooperation_status}</StatusPill>
                <small>报价 {supplier.quote_count} · 中标 {supplier.win_count}</small>
              </span>
              <span className={`proc-score-badge ${scoreTone(supplier.performance.level)}`} title={scoreExplanation(supplier)}>
                <strong className="tnum">{Number(supplier.performance.score).toFixed(1)}</strong>
                <small>{supplier.performance.level}</small>
              </span>
            </button>
            <span className="proc-supplier-actions">
              <IconButton label={`编辑供应商 ${supplier.name}`} onClick={() => openEdit(supplier)}>
                <Pencil size={14} />
              </IconButton>
              <IconButton label={`删除供应商 ${supplier.name}`} tone="danger" onClick={() => { setDeleteTarget(supplier); setDeleteError(null); }}>
                <Trash2 size={14} />
              </IconButton>
            </span>
          </article>
        ))}
        {totalPages > 1 ? (
          <footer className="proc-task-pagination">
            <button type="button" className="proc-icon-button" title="上一页" aria-label="上一页" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>‹</button>
            <span>{page + 1} / {totalPages}（共 {total} 家）</span>
            <button type="button" className="proc-icon-button" title="下一页" aria-label="下一页" disabled={page + 1 >= totalPages} onClick={() => setPage((value) => Math.min(totalPages - 1, value + 1))}>›</button>
          </footer>
        ) : null}
      </div>

      {editing ? (
        <Modal
          titleId="supplier-form-title"
          title={editing === "new" ? "新建供应商" : "编辑供应商档案"}
          icon={<Building2 size={18} />}
          size="lg"
          busy={formBusy}
          onClose={() => setEditing(null)}
          dialogRef={formDialogRef}
          footer={
            <>
              <Button variant="secondary" onClick={() => setEditing(null)} disabled={formBusy}>取消</Button>
              <Button variant="primary" icon={<CheckCircle2 size={15} />} loading={formBusy} onClick={() => void save()}>{editing === "new" ? "创建" : "保存"}</Button>
            </>
          }
        >
          <div className="proc-dialog-form">
            <label className="proc-field proc-field-wide">
              <span>供应商名称 <b>*</b></span>
              <input ref={formNameRef} className="proc-input" value={form.name || ""} disabled={editing !== "new"} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="例如 华东优包" />
              {editing !== "new" ? <small className="proc-field-hint">名称不可修改（报价历史按名称关联）</small> : null}
            </label>
            <label className="proc-field">
              <span>联系人</span>
              <input className="proc-input" value={form.contact_person || ""} onChange={(event) => setForm((current) => ({ ...current, contact_person: event.target.value }))} placeholder="姓名" />
            </label>
            <label className="proc-field">
              <span>电话</span>
              <input className="proc-input mono" value={form.phone || ""} onChange={(event) => setForm((current) => ({ ...current, phone: event.target.value }))} placeholder="手机或座机" />
            </label>
            <label className="proc-field">
              <span>邮箱</span>
              <input className="proc-input mono" value={form.email || ""} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} placeholder="name@example.com" />
            </label>
            <label className="proc-field">
              <span>主营品类</span>
              <input className="proc-input" value={form.main_categories || ""} onChange={(event) => setForm((current) => ({ ...current, main_categories: event.target.value }))} placeholder="逗号分隔，例如 快递袋,气泡膜" />
            </label>
            <label className="proc-field proc-field-wide">
              <span>地址</span>
              <input className="proc-input" value={form.address || ""} onChange={(event) => setForm((current) => ({ ...current, address: event.target.value }))} placeholder="收货或工厂地址" />
            </label>
            <label className="proc-field">
              <span>合作状态</span>
              <select className="proc-input" value={form.status || "ACTIVE"} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value as SupplierStatus }))}>
                <option value="ACTIVE">合作中</option>
                <option value="PAUSED">已暂停</option>
                <option value="BLACKLISTED">黑名单</option>
              </select>
              <small className="proc-field-hint">暂停/黑名单后绩效按冻结规则折算</small>
            </label>
            <label className="proc-field proc-field-wide">
              <span>备注</span>
              <textarea className="proc-input" value={form.notes || ""} onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))} rows={3} placeholder="合作条款、账期等备注" />
            </label>
          </div>
          {formError ? <p className="proc-dialog-error" role="alert">{formError}</p> : null}
        </Modal>
      ) : null}

      {deleteTarget ? (
        <Modal
          titleId="delete-supplier-title"
          title="删除供应商档案"
          icon={<Trash2 size={18} />}
          tone="danger"
          busy={deleteBusy}
          onClose={() => setDeleteTarget(null)}
          dialogRef={deleteDialogRef}
          initialFocusRef={deleteCancelRef}
          footer={
            <>
              <Button variant="secondary" ref={deleteCancelRef} onClick={() => setDeleteTarget(null)} disabled={deleteBusy}>取消</Button>
              <Button variant="danger" icon={<Trash2 size={15} />} loading={deleteBusy} onClick={() => void confirmDelete()}>删除档案</Button>
            </>
          }
        >
          <div className="proc-dialog-target">
            <strong>{deleteTarget.name}</strong>
            <span>{deleteTarget.cooperation_status} · {deleteTarget.quote_count} 次报价</span>
          </div>
          <p className="proc-muted">有关联报价历史的供应商会被拒绝删除（删除保护），可将状态改为暂停或黑名单。</p>
          {deleteError ? <p className="proc-dialog-error" role="alert">{deleteError}</p> : null}
        </Modal>
      ) : null}

      {profileId ? (
        <Drawer
          titleId="supplier-profile-title"
          asideRef={drawerRef}
          title={selectedSupplier?.name || profile?.name || "供应商档案"}
          subtitle={dashIfEmpty(selectedSupplier?.main_categories || profile?.main_categories)}
          icon={<Building2 size={18} />}
          width="lg"
          closeLabel="关闭档案"
          onClose={() => setProfileId(null)}
        >
          {profileQuery.isPending ? <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在聚合档案…</div> : null}
          {profileQuery.isError ? (
            <ErrorState
              title="档案加载失败"
              detail={profileQuery.error instanceof Error ? profileQuery.error.message : "未知错误"}
              onRetry={() => void profileQuery.refetch()}
            />
          ) : null}
          {profile ? <SupplierProfileBody profile={profile} onOpenTask={onOpenTask} /> : null}
        </Drawer>
      ) : null}
    </CenterPage>
  );
}

function ScoreBars({ label, value }: { label: string; value: string | number }) {
  const width = Math.min(100, Number(value) || 0);
  return (
    <div className="proc-score-bar">
      <span>{label}</span>
      <div className="proc-score-bar-track"><i style={{ width: `${width}%` }} /></div>
      <b className="tnum">{value}</b>
    </div>
  );
}

function SupplierProfileBody({ profile, onOpenTask }: { profile: SupplierProfile; onOpenTask: (taskId: string) => void }) {
  const performance = profile.performance;
  return (
    <div className="proc-supplier-profile">
      <section className="proc-profile-section">
        <header>
          <h3><Users size={15} /> 绩效评分</h3>
          <span className={`proc-score-badge ${scoreTone(performance.level)}`}>
            <strong className="tnum">{Number(performance.score).toFixed(1)}</strong>
            <small>{performance.level}</small>
          </span>
        </header>
        <p className="proc-score-note">{scoreExplanation(profile)}</p>
        <div className="proc-score-bars">
          <ScoreBars label="中标率得分" value={performance.win_rate_score} />
          <ScoreBars label="活跃度得分" value={performance.activity_score} />
          <ScoreBars label="合作状态得分" value={performance.status_score} />
        </div>
        <p className="proc-eval-note">口径：中标率满 60 分但报价少于 3 次按 0.5 折减；黑名单强制封顶 30 分。</p>
      </section>

      <section className="proc-profile-section">
        <header><h3><UserRound size={15} /> 档案信息</h3></header>
        <div className="proc-fact-grid is-2">
          <Fact label="联系人">{dashIfEmpty(profile.contact_person)}</Fact>
          <Fact label="电话" mono>{dashIfEmpty(profile.phone)}</Fact>
          <Fact label="邮箱" mono>{dashIfEmpty(profile.email)}</Fact>
          <Fact label="地址">{dashIfEmpty(profile.address)}</Fact>
          <Fact label="合作状态">{profile.cooperation_status}</Fact>
          <Fact label="备注">{dashIfEmpty(profile.notes)}</Fact>
        </div>
      </section>

      <section className="proc-profile-section">
        <header>
          <h3><Archive size={15} /> 参与记录</h3>
          <small>{profile.quote_count} 次报价 · {profile.win_count} 次中标</small>
        </header>
        <div className="proc-supplier-metrics">
          <div><strong className="tnum">{profile.quote_count}</strong><small>报价次数</small></div>
          <div><strong className="tnum is-accent">{profile.win_count}</strong><small>中标次数</small></div>
          <div><strong className="tnum">{Number(profile.win_rate) * 100}%</strong><small>中标率</small></div>
        </div>
        <h4>参与物料</h4>
        <div className="proc-tag-row">
          {profile.items.length ? profile.items.map((item) => <span className="proc-tag" key={item}>{item}</span>) : DASH}
        </div>
        <h4>最近报价</h4>
        <ul className="proc-supplier-quote-list">
          {profile.recent_quotes.length ? profile.recent_quotes.map((quote) => (
            <li key={quote.quote_id}>
              <button className="w-full text-left flex flex-col gap-0.5" type="button" onClick={() => onOpenTask(quote.task_id)} title="打开采购任务">
                <strong>{quote.item_name || "未知物料"}</strong>
                <small className="mono">{quote.task_reference || (quote.task_id ? `任务 ${quote.task_id.slice(0, 8)}` : "—")} · {quote.source_filename}</small>
              </button>
            </li>
          )) : <li className="proc-muted">暂无报价记录</li>}
        </ul>
      </section>
    </div>
  );
}
