import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  Building2,
  LoaderCircle,
  Pencil,
  Plus,
  Search,
  Trash2,
  UserRound,
  Users,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import { procurementApi } from "./api";
import { useEscape } from "./useEscape";
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

  const pageSize = 50;
  const suppliersQuery = useQuery({
    queryKey: ["procurement-suppliers", search.trim(), status, page],
    queryFn: () => procurementApi.suppliers(search.trim() || undefined, status || undefined, page, pageSize),
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

  useEscape(!!editing, () => setEditing(null), formBusy);
  useEscape(!!deleteTarget, () => setDeleteTarget(null), deleteBusy);
  useEscape(!!profileId, () => setProfileId(null), false);

  return (
    <section className="proc-main">
      <header className="proc-page-head">
        <div>
          <h1>供应商管理</h1>
          <p>档案与绩效评分（中标率 / 活跃度 / 合作状态派生）</p>
        </div>
        <button className="proc-button" type="button" onClick={openCreate}>
          <Plus size={15} />新建供应商
        </button>
      </header>

      <div className="proc-toolbar" role="toolbar">
        <label className="proc-search proc-toolbar-search">
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

      <div className="proc-supplier-list" aria-busy={suppliersQuery.isPending}>
        {suppliersQuery.isPending ? (
          <div className="proc-loading-state"><LoaderCircle className="spin" size={18} />正在加载供应商档案…</div>
        ) : null}
        {suppliersQuery.isError ? (
          <section className="proc-empty-state compact" role="alert">
            <AlertTriangle size={26} />
            <h2>供应商档案加载失败</h2>
            <p>{suppliersQuery.error instanceof Error ? suppliersQuery.error.message : "未知错误"}</p>
            <button className="proc-button secondary" type="button" onClick={() => void suppliersQuery.refetch()}>重新加载</button>
          </section>
        ) : null}
        {!suppliersQuery.isPending && !suppliersQuery.isError && !suppliers.length ? (
          <div className="proc-empty-state">
            <Archive size={30} />
            <h2>{search || status ? "没有匹配的供应商" : "还没有供应商档案"}</h2>
            <p>{search || status ? "调整搜索条件或状态筛选后重试。" : "新建供应商档案后，报价与中标记录将按名称自动关联。"}</p>
            <button className="proc-button" type="button" onClick={openCreate}><Plus size={15} />新建供应商</button>
          </div>
        ) : null}
        {suppliers.map((supplier) => {
          const tone = scoreTone(supplier.performance.level);
          return (
            <article className="proc-supplier-card" key={supplier.id}>
              <button
                type="button"
                className="proc-supplier-card-main"
                onClick={() => {
                  setProfileId(supplier.id);
                }}
                aria-label={`查看供应商档案 ${supplier.name}`}
              >
                <span className="proc-supplier-avatar"><Building2 size={18} /></span>
                <span className="proc-supplier-info">
                  <strong>{supplier.name}</strong>
                  <small>{supplier.main_categories || "未填写主营品类"}</small>
                  <small>{supplier.contact_person ? `${supplier.contact_person} · ${supplier.phone || "无电话"}` : "未填写联系人"}</small>
                </span>
                <span className="proc-supplier-stats">
                  <small>报价 {supplier.quote_count} · 中标 {supplier.win_count}</small>
                  <i className={supplier.cooperation_status === "合作中" ? "success" : supplier.cooperation_status === "黑名单" ? "danger" : supplier.cooperation_status === "已暂停" ? "warning" : "neutral"}>
                    {supplier.cooperation_status}
                  </i>
                </span>
                <span className={`proc-score-badge ${tone}`}>
                  <strong>{Number(supplier.performance.score).toFixed(1)}</strong>
                  <small>{supplier.performance.level}</small>
                </span>
              </button>
              <span className="proc-supplier-actions">
                <button
                  className="proc-icon-button compact"
                  type="button"
                  title="编辑档案"
                  aria-label={`编辑供应商 ${supplier.name}`}
                  onClick={() => openEdit(supplier)}
                >
                  <Pencil size={14} />
                </button>
                <button
                  className="proc-icon-button compact danger-hover"
                  type="button"
                  title="删除档案"
                  aria-label={`删除供应商 ${supplier.name}`}
                  onClick={() => {
                    setDeleteTarget(supplier);
                    setDeleteError(null);
                  }}
                >
                  <Trash2 size={14} />
                </button>
              </span>
            </article>
          );
        })}
        {totalPages > 1 ? (
          <footer className="proc-task-pagination">
            <button type="button" title="上一页" aria-label="上一页" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>‹</button>
            <span>{page + 1} / {totalPages}（共 {total} 家）</span>
            <button type="button" title="下一页" aria-label="下一页" disabled={page + 1 >= totalPages} onClick={() => setPage((value) => Math.min(totalPages - 1, value + 1))}>›</button>
          </footer>
        ) : null}
      </div>

      {editing ? (
        <div
          className="proc-drawer-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !formBusy) setEditing(null);
          }}
        >
          <section className="proc-confirm-dialog proc-supplier-dialog" role="dialog" aria-modal="true" aria-labelledby="supplier-form-title">
            <header>
              <div><Building2 size={17} /><h2 id="supplier-form-title">{editing === "new" ? "新建供应商" : "编辑供应商档案"}</h2></div>
              <button className="proc-icon-button compact" type="button" title="关闭" aria-label="关闭" onClick={() => setEditing(null)} disabled={formBusy}><X size={16} /></button>
            </header>
            <div className="proc-supplier-form">
              <label className="proc-field proc-span-2">
                <span>供应商名称 <b>*</b></span>
                <input value={form.name || ""} disabled={editing !== "new"} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="例如 华东优包" />
                {editing !== "new" ? <small>名称不可修改（报价历史按名称关联）</small> : null}
              </label>
              <label className="proc-field">
                <span>联系人</span>
                <input value={form.contact_person || ""} onChange={(event) => setForm((current) => ({ ...current, contact_person: event.target.value }))} placeholder="姓名" />
              </label>
              <label className="proc-field">
                <span>电话</span>
                <input value={form.phone || ""} onChange={(event) => setForm((current) => ({ ...current, phone: event.target.value }))} placeholder="手机或座机" />
              </label>
              <label className="proc-field">
                <span>邮箱</span>
                <input value={form.email || ""} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} placeholder="name@example.com" />
              </label>
              <label className="proc-field">
                <span>主营品类</span>
                <input value={form.main_categories || ""} onChange={(event) => setForm((current) => ({ ...current, main_categories: event.target.value }))} placeholder="逗号分隔，例如 快递袋,气泡膜" />
              </label>
              <label className="proc-field proc-span-2">
                <span>地址</span>
                <input value={form.address || ""} onChange={(event) => setForm((current) => ({ ...current, address: event.target.value }))} placeholder="收货或工厂地址" />
              </label>
              <label className="proc-field">
                <span>合作状态</span>
                <select value={form.status || "ACTIVE"} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value as SupplierStatus }))}>
                  <option value="ACTIVE">合作中</option>
                  <option value="PAUSED">已暂停</option>
                  <option value="BLACKLISTED">黑名单</option>
                </select>
                <small>暂停/黑名单后绩效按冻结规则折算</small>
              </label>
              <label className="proc-field proc-span-2">
                <span>备注</span>
                <textarea value={form.notes || ""} onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))} rows={3} placeholder="合作条款、账期等备注" />
              </label>
              {formError ? <p className="proc-form-error proc-span-2" role="alert">{formError}</p> : null}
            </div>
            <footer>
              <button className="proc-button secondary" type="button" onClick={() => setEditing(null)} disabled={formBusy}>取消</button>
              <button className="proc-button" type="button" onClick={() => void save()} disabled={formBusy}>
                {formBusy ? <LoaderCircle className="spin" size={15} /> : <SaveIcon />}{editing === "new" ? "创建" : "保存"}
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {deleteTarget ? (
        <div
          className="proc-drawer-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !deleteBusy) setDeleteTarget(null);
          }}
        >
          <section className="proc-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-supplier-title">
            <header>
              <div><Trash2 size={17} /><h2 id="delete-supplier-title">删除供应商档案</h2></div>
              <button className="proc-icon-button compact" type="button" title="关闭" aria-label="关闭" onClick={() => setDeleteTarget(null)} disabled={deleteBusy}><X size={16} /></button>
            </header>
            <div className="proc-delete-target">
              <strong>{deleteTarget.name}</strong>
              <span>{deleteTarget.cooperation_status} · {deleteTarget.quote_count} 次报价</span>
            </div>
            <p className="proc-confirm-warning">有关联报价历史的供应商会被拒绝删除（删除保护），可将状态改为暂停或黑名单。</p>
            {deleteError ? <p className="proc-form-error" role="alert">{deleteError}</p> : null}
            <footer>
              <button className="proc-button secondary" type="button" onClick={() => setDeleteTarget(null)} disabled={deleteBusy}>取消</button>
              <button className="proc-button danger" type="button" onClick={() => void confirmDelete()} disabled={deleteBusy}>
                {deleteBusy ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}删除档案
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {profileId ? (
        <div
          className="proc-drawer-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setProfileId(null);
          }}
        >
          <aside className="proc-config-drawer proc-supplier-drawer" role="dialog" aria-modal="true" aria-labelledby="supplier-profile-title">
            <header className="proc-config-head">
              <div>
                <span className="proc-config-icon"><Building2 size={17} /></span>
                <div>
                  <h2 id="supplier-profile-title">{selectedSupplier?.name || profile?.name || "供应商档案"}</h2>
                  <p>{selectedSupplier?.main_categories || profile?.main_categories || "主营品类未填写"}</p>
                </div>
              </div>
              <button className="proc-icon-button compact" type="button" title="关闭档案" aria-label="关闭档案" onClick={() => setProfileId(null)}><X size={16} /></button>
            </header>
            <div className="proc-config-body">
              {profileQuery.isPending ? <div className="proc-config-loading"><LoaderCircle className="spin" size={18} />正在聚合档案…</div> : null}
              {profileQuery.isError ? (
                <section className="proc-config-error" role="alert">
                  <strong>档案加载失败</strong>
                  <span>{profileQuery.error instanceof Error ? profileQuery.error.message : "未知错误"}</span>
                  <button className="proc-button secondary" type="button" onClick={() => void profileQuery.refetch()}>重新加载</button>
                </section>
              ) : null}
              {profile ? <SupplierProfileBody profile={profile} onOpenTask={onOpenTask} /> : null}
            </div>
          </aside>
        </div>
      ) : null}
    </section>
  );
}

function SaveIcon() {
  return <Plus size={15} />;
}

function SupplierProfileBody({ profile, onOpenTask }: { profile: SupplierProfile; onOpenTask: (taskId: string) => void }) {
  const performance = profile.performance;
  const tone = scoreTone(performance.level);
  return (
    <div className="proc-supplier-profile">
      <section className="proc-eval-band">
        <header>
          <div><Users size={15} /><h3>绩效评分</h3></div>
          <span className={`proc-score-badge ${tone}`}><strong>{Number(performance.score).toFixed(1)}</strong><small>{performance.level}</small></span>
        </header>
        <div className="proc-score-bars">
          <div><span>中标率得分</span><i style={{ width: `${Math.min(100, Number(performance.win_rate_score))}%` }} /><small>{performance.win_rate_score}</small></div>
          <div><span>活跃度得分</span><i style={{ width: `${Math.min(100, Number(performance.activity_score))}%` }} /><small>{performance.activity_score}</small></div>
          <div><span>合作状态得分</span><i style={{ width: `${Math.min(100, Number(performance.status_score))}%` }} /><small>{performance.status_score}</small></div>
        </div>
        <p className="proc-eval-note">口径：中标率满 60 分但报价少于 3 次按 0.5 折减；黑名单强制封顶 30 分。</p>
      </section>
      <section className="proc-report-section">
        <header><div><UserRound size={15} /><h3>档案信息</h3></div></header>
        <dl className="proc-facts-grid">
          <div><dt>联系人</dt><dd>{profile.contact_person || "—"}</dd></div>
          <div><dt>电话</dt><dd>{profile.phone || "—"}</dd></div>
          <div><dt>邮箱</dt><dd>{profile.email || "—"}</dd></div>
          <div><dt>地址</dt><dd>{profile.address || "—"}</dd></div>
          <div><dt>合作状态</dt><dd>{profile.cooperation_status}</dd></div>
          <div><dt>备注</dt><dd>{profile.notes || "—"}</dd></div>
        </dl>
      </section>
      <section className="proc-report-section">
        <header><div><Archive size={15} /><h3>参与记录</h3></div><span>{profile.quote_count} 次报价 · {profile.win_count} 次中标 · 中标率 {profile.win_rate}</span></header>
        <div className="proc-supplier-metrics">
          <div><strong>{profile.quote_count}</strong><small>报价次数</small></div>
          <div><strong>{profile.win_count}</strong><small>中标次数</small></div>
          <div><strong>{Number(profile.win_rate) * 100}%</strong><small>中标率</small></div>
        </div>
        <h4>参与物料</h4>
        <div className="proc-tag-row">
          {profile.items.length ? profile.items.map((item) => <span className="proc-tag" key={item}>{item}</span>) : <span className="proc-muted">暂无</span>}
        </div>
        <h4>最近报价</h4>
        <ul className="proc-quote-list">
          {profile.recent_quotes.length ? profile.recent_quotes.map((quote) => (
            <li key={quote.quote_id}>
              <button type="button" onClick={() => onOpenTask(quote.task_id)} title="打开采购任务">
                <strong>{quote.item_name || "未知物料"}</strong>
                <small>{quote.task_reference || quote.task_id} · {quote.source_filename}</small>
              </button>
            </li>
          )) : <li className="proc-muted">暂无报价记录</li>}
        </ul>
      </section>
    </div>
  );
}
