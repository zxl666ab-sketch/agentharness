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
    <div className="proc-center-page flex flex-col gap-6 p-6 max-w-7xl mx-auto w-full">
      <header className="proc-page-head flex flex-wrap items-center justify-between gap-4 pb-4 border-b border-border">
        <div>
          <h1 className="text-xl font-bold text-text tracking-tight flex items-center gap-2">
            <Building2 className="w-5 h-5 text-accent" />
            供应商管理
          </h1>
          <p className="text-xs text-text-muted mt-1">档案与绩效评分（中标率 / 活跃度 / 合作状态派生）</p>
        </div>
        <button className="proc-button inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs" type="button" onClick={openCreate}>
          <Plus size={15} />新建供应商
        </button>
      </header>

      <div className="proc-toolbar flex flex-wrap items-center gap-3" role="toolbar">
        <label className="proc-search proc-toolbar-search flex-1 min-w-[240px] flex items-center gap-2 px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus-within:border-accent">
          <Search size={15} className="text-text-muted" />
          <input
            aria-label="搜索供应商"
            className="w-full bg-transparent border-none outline-none text-xs text-text placeholder:text-text-muted"
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
          className="proc-select px-3 py-2 rounded-lg border border-border bg-surface text-xs text-text focus:outline-accent"
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

      <div className="proc-supplier-list grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5" aria-busy={suppliersQuery.isPending}>
        {suppliersQuery.isPending ? (
          <div className="proc-loading-state col-span-full py-12 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={18} />正在加载供应商档案…</div>
        ) : null}
        {suppliersQuery.isError ? (
          <section className="proc-empty-state compact col-span-full py-10 flex flex-col items-center justify-center gap-2 text-center text-xs" role="alert">
            <AlertTriangle size={26} className="text-danger" />
            <h2 className="text-sm font-semibold text-text">供应商档案加载失败</h2>
            <p className="text-text-muted">{suppliersQuery.error instanceof Error ? suppliersQuery.error.message : "未知错误"}</p>
            <button className="proc-button secondary px-3 py-1.5 rounded-lg border border-border text-xs font-medium hover:bg-surface-subtle" type="button" onClick={() => void suppliersQuery.refetch()}>重新加载</button>
          </section>
        ) : null}
        {!suppliersQuery.isPending && !suppliersQuery.isError && !suppliers.length ? (
          <div className="proc-empty-state col-span-full py-16 flex flex-col items-center justify-center gap-2 text-center text-xs text-text-muted">
            <Archive size={30} className="text-text-muted" />
            <h2 className="text-sm font-semibold text-text">{search || status ? "没有匹配的供应商" : "还没有供应商档案"}</h2>
            <p className="max-w-md">{search || status ? "调整搜索条件或状态筛选后重试。" : "新建供应商档案后，报价与中标记录将按名称自动关联。"}</p>
            <button className="proc-button inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong transition-all shadow-xs" type="button" onClick={openCreate}><Plus size={15} />新建供应商</button>
          </div>
        ) : null}
        {suppliers.map((supplier) => {
          const tone = scoreTone(supplier.performance.level);
          return (
            <article className="proc-supplier-card glass-panel bg-surface/80 hover:bg-surface border border-border/80 hover:border-accent/40 rounded-xl p-4 shadow-sm hover:shadow-md transition-all duration-150 flex items-start justify-between gap-3 relative group" key={supplier.id}>
              <button
                type="button"
                className="proc-supplier-card-main flex-1 text-left flex flex-col gap-3 cursor-pointer"
                onClick={() => {
                  setProfileId(supplier.id);
                }}
                aria-label={`查看供应商档案 ${supplier.name}`}
              >
                <div className="flex items-start gap-3">
                  <span className="proc-supplier-avatar w-10 h-10 rounded-xl bg-accent-soft text-accent flex items-center justify-center flex-shrink-0"><Building2 size={20} /></span>
                  <span className="proc-supplier-info flex flex-col min-w-0">
                    <strong className="text-sm font-bold text-text truncate">{supplier.name}</strong>
                    <small className="text-xs text-text-secondary truncate mt-0.5">{supplier.main_categories || "未填写主营品类"}</small>
                    <small className="text-[11px] text-text-muted truncate mt-0.5">{supplier.contact_person ? `${supplier.contact_person} · ${supplier.phone || "无电话"}` : "未填写联系人"}</small>
                  </span>
                </div>
                <div className="flex items-center justify-between pt-2 border-t border-border/40 text-xs">
                  <span className="proc-supplier-stats flex items-center gap-2 text-text-muted text-[11px]">
                    <small>报价 {supplier.quote_count} · 中标 {supplier.win_count}</small>
                    <i className={`not-italic font-medium px-2 py-0.5 rounded-full border text-[10px] ${supplier.cooperation_status === "合作中" ? "bg-accent-soft text-accent border-accent/30" : supplier.cooperation_status === "黑名单" ? "bg-danger-soft text-danger border-danger/30" : supplier.cooperation_status === "已暂停" ? "bg-warning-soft text-warning border-warning/30" : "bg-surface-subtle text-text-muted border-border"}`}>
                      {supplier.cooperation_status}
                    </i>
                  </span>
                  <span className={`proc-score-badge ${tone} flex flex-col items-center justify-center px-2 py-1 rounded-lg border text-xs`}>
                    <strong className="font-mono font-bold">{Number(supplier.performance.score).toFixed(1)}</strong>
                    <small className="text-[10px] opacity-80">{supplier.performance.level}</small>
                  </span>
                </div>
              </button>
              <span className="proc-supplier-actions flex flex-col gap-1 opacity-80 group-hover:opacity-100 transition-opacity">
                <button
                  className="proc-icon-button compact w-7 h-7 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-text hover:bg-surface-subtle transition-all"
                  type="button"
                  title="编辑档案"
                  aria-label={`编辑供应商 ${supplier.name}`}
                  onClick={() => openEdit(supplier)}
                >
                  <Pencil size={13} />
                </button>
                <button
                  className="proc-icon-button compact danger-hover w-7 h-7 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-danger hover:border-danger/30 hover:bg-danger-soft transition-all"
                  type="button"
                  title="删除档案"
                  aria-label={`删除供应商 ${supplier.name}`}
                  onClick={() => {
                    setDeleteTarget(supplier);
                    setDeleteError(null);
                  }}
                >
                  <Trash2 size={13} />
                </button>
              </span>
            </article>
          );
        })}
        {totalPages > 1 ? (
          <footer className="proc-task-pagination col-span-full flex items-center justify-center gap-3 pt-4 text-xs text-text-muted">
            <button className="w-8 h-8 rounded-lg border border-border flex items-center justify-center text-text hover:bg-surface-subtle disabled:opacity-40" type="button" title="上一页" aria-label="上一页" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>‹</button>
            <span className="font-medium">{page + 1} / {totalPages}（共 {total} 家）</span>
            <button className="w-8 h-8 rounded-lg border border-border flex items-center justify-center text-text hover:bg-surface-subtle disabled:opacity-40" type="button" title="下一页" aria-label="下一页" disabled={page + 1 >= totalPages} onClick={() => setPage((value) => Math.min(totalPages - 1, value + 1))}>›</button>
          </footer>
        ) : null}
      </div>

      {editing ? (
        <div
          className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !formBusy) setEditing(null);
          }}
        >
          <section className="proc-confirm-dialog proc-supplier-dialog glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-lg w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="supplier-form-title">
            <header className="flex items-center justify-between pb-3 border-b border-border/60">
              <div className="flex items-center gap-2 text-text font-bold text-base"><Building2 size={18} className="text-accent" /><h2 id="supplier-form-title">{editing === "new" ? "新建供应商" : "编辑供应商档案"}</h2></div>
              <button className="proc-icon-button compact w-7 h-7 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-text" type="button" title="关闭" aria-label="关闭" onClick={() => setEditing(null)} disabled={formBusy}><X size={16} /></button>
            </header>
            <div className="proc-supplier-form grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
              <label className="proc-field proc-span-2 sm:col-span-2 flex flex-col gap-1 font-medium text-text">
                <span>供应商名称 <b>*</b></span>
                <input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm disabled:opacity-60" value={form.name || ""} disabled={editing !== "new"} onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))} placeholder="例如 华东优包" />
                {editing !== "new" ? <small className="text-[11px] text-text-muted">名称不可修改（报价历史按名称关联）</small> : null}
              </label>
              <label className="proc-field flex flex-col gap-1 font-medium text-text">
                <span>联系人</span>
                <input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" value={form.contact_person || ""} onChange={(event) => setForm((current) => ({ ...current, contact_person: event.target.value }))} placeholder="姓名" />
              </label>
              <label className="proc-field flex flex-col gap-1 font-medium text-text">
                <span>电话</span>
                <input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm font-mono" value={form.phone || ""} onChange={(event) => setForm((current) => ({ ...current, phone: event.target.value }))} placeholder="手机或座机" />
              </label>
              <label className="proc-field flex flex-col gap-1 font-medium text-text">
                <span>邮箱</span>
                <input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm font-mono" value={form.email || ""} onChange={(event) => setForm((current) => ({ ...current, email: event.target.value }))} placeholder="name@example.com" />
              </label>
              <label className="proc-field flex flex-col gap-1 font-medium text-text">
                <span>主营品类</span>
                <input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" value={form.main_categories || ""} onChange={(event) => setForm((current) => ({ ...current, main_categories: event.target.value }))} placeholder="逗号分隔，例如 快递袋,气泡膜" />
              </label>
              <label className="proc-field proc-span-2 sm:col-span-2 flex flex-col gap-1 font-medium text-text">
                <span>地址</span>
                <input className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" value={form.address || ""} onChange={(event) => setForm((current) => ({ ...current, address: event.target.value }))} placeholder="收货或工厂地址" />
              </label>
              <label className="proc-field flex flex-col gap-1 font-medium text-text">
                <span>合作状态</span>
                <select className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" value={form.status || "ACTIVE"} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value as SupplierStatus }))}>
                  <option value="ACTIVE">合作中</option>
                  <option value="PAUSED">已暂停</option>
                  <option value="BLACKLISTED">黑名单</option>
                </select>
                <small className="text-[11px] text-text-muted">暂停/黑名单后绩效按冻结规则折算</small>
              </label>
              <label className="proc-field proc-span-2 sm:col-span-2 flex flex-col gap-1 font-medium text-text">
                <span>备注</span>
                <textarea className="px-3 py-2 rounded-lg border border-border bg-surface-subtle text-text focus:outline-accent text-sm" value={form.notes || ""} onChange={(event) => setForm((current) => ({ ...current, notes: event.target.value }))} rows={3} placeholder="合作条款、账期等备注" />
              </label>
              {formError ? <p className="proc-form-error proc-span-2 col-span-full text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{formError}</p> : null}
            </div>
            <footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60">
              <button className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => setEditing(null)} disabled={formBusy}>取消</button>
              <button className="proc-button px-4 py-1.5 rounded-lg text-xs font-semibold bg-accent text-white hover:bg-accent-strong inline-flex items-center gap-1.5 shadow-xs" type="button" onClick={() => void save()} disabled={formBusy}>
                {formBusy ? <LoaderCircle className="spin" size={15} /> : <SaveIcon />}{editing === "new" ? "创建" : "保存"}
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {deleteTarget ? (
        <div
          className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex items-center justify-center p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !deleteBusy) setDeleteTarget(null);
          }}
        >
          <section className="proc-confirm-dialog glass-panel bg-surface border border-border/80 rounded-2xl p-6 shadow-2xl max-w-md w-full mx-4 space-y-4 animate-in fade-in zoom-in-95 duration-150" role="dialog" aria-modal="true" aria-labelledby="delete-supplier-title">
            <header className="flex items-center justify-between pb-3 border-b border-border/60">
              <div className="flex items-center gap-2 text-text font-bold text-base"><Trash2 size={18} className="text-danger" /><h2 id="delete-supplier-title">删除供应商档案</h2></div>
              <button className="proc-icon-button compact w-7 h-7 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-text" type="button" title="关闭" aria-label="关闭" onClick={() => setDeleteTarget(null)} disabled={deleteBusy}><X size={16} /></button>
            </header>
            <div className="proc-delete-target p-3 rounded-lg bg-surface-subtle border border-border flex flex-col gap-0.5 text-xs">
              <strong className="font-bold text-sm text-text">{deleteTarget.name}</strong>
              <span className="text-text-muted">{deleteTarget.cooperation_status} · {deleteTarget.quote_count} 次报价</span>
            </div>
            <p className="proc-confirm-warning text-xs text-text-muted">有关联报价历史的供应商会被拒绝删除（删除保护），可将状态改为暂停或黑名单。</p>
            {deleteError ? <p className="proc-form-error text-xs text-danger font-medium p-2.5 rounded-lg bg-danger-soft border border-danger/30" role="alert">{deleteError}</p> : null}
            <footer className="flex items-center justify-end gap-2 pt-3 border-t border-border/60">
              <button className="proc-button secondary px-3.5 py-1.5 rounded-lg border border-border text-xs font-medium text-text hover:bg-surface-subtle" type="button" onClick={() => setDeleteTarget(null)} disabled={deleteBusy}>取消</button>
              <button className="proc-button danger px-4 py-1.5 rounded-lg text-xs font-semibold bg-danger text-white hover:bg-rose-700 inline-flex items-center gap-1.5 shadow-xs" type="button" onClick={() => void confirmDelete()} disabled={deleteBusy}>
                {deleteBusy ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}删除档案
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {profileId ? (
        <div
          className="proc-drawer-backdrop fixed inset-0 z-50 bg-black/50 backdrop-blur-xs flex justify-end"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setProfileId(null);
          }}
        >
          <aside className="proc-config-drawer proc-supplier-drawer w-full max-w-lg bg-surface border-l border-border shadow-2xl z-50 flex flex-col overflow-y-auto animate-in slide-in-from-right duration-200" role="dialog" aria-modal="true" aria-labelledby="supplier-profile-title">
            <header className="proc-config-head flex items-start justify-between gap-3 p-5 border-b border-border/60 bg-surface/90 backdrop-blur-xs sticky top-0 z-10">
              <div className="flex items-center gap-3">
                <span className="proc-config-icon w-9 h-9 rounded-lg bg-accent-soft text-accent flex items-center justify-center"><Building2 size={18} /></span>
                <div>
                  <h2 className="text-base font-bold text-text" id="supplier-profile-title">{selectedSupplier?.name || profile?.name || "供应商档案"}</h2>
                  <p className="text-xs text-text-muted mt-0.5">{selectedSupplier?.main_categories || profile?.main_categories || "主营品类未填写"}</p>
                </div>
              </div>
              <button className="proc-icon-button compact w-7 h-7 rounded-lg border border-border flex items-center justify-center text-text-muted hover:text-text" type="button" title="关闭档案" aria-label="关闭档案" onClick={() => setProfileId(null)}><X size={16} /></button>
            </header>
            <div className="proc-config-body p-5 flex flex-col gap-5">
              {profileQuery.isPending ? <div className="proc-config-loading py-12 flex items-center justify-center gap-2 text-text-muted text-xs"><LoaderCircle className="spin" size={18} />正在聚合档案…</div> : null}
              {profileQuery.isError ? (
                <section className="proc-config-error p-4 rounded-xl bg-danger-soft border border-danger/30 flex flex-col gap-2 text-xs text-danger" role="alert">
                  <strong className="font-semibold">档案加载失败</strong>
                  <span>{profileQuery.error instanceof Error ? profileQuery.error.message : "未知错误"}</span>
                  <button className="proc-button secondary px-3 py-1.5 rounded-lg border border-border text-xs font-medium self-start mt-1" type="button" onClick={() => void profileQuery.refetch()}>重新加载</button>
                </section>
              ) : null}
              {profile ? <SupplierProfileBody profile={profile} onOpenTask={onOpenTask} /> : null}
            </div>
          </aside>
        </div>
      ) : null}
    </div>
  );
}

function SaveIcon() {
  return <Plus size={15} />;
}

function SupplierProfileBody({ profile, onOpenTask }: { profile: SupplierProfile; onOpenTask: (taskId: string) => void }) {
  const performance = profile.performance;
  const tone = scoreTone(performance.level);
  return (
    <div className="proc-supplier-profile flex flex-col gap-5">
      <section className="proc-eval-band glass-panel bg-surface/80 p-4 rounded-xl border border-border/80 flex flex-col gap-3">
        <header className="flex items-center justify-between pb-2 border-b border-border/40">
          <div className="flex items-center gap-2"><Users size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">绩效评分</h3></div>
          <span className={`proc-score-badge ${tone} flex flex-col items-center justify-center px-2.5 py-1 rounded-lg border text-xs`}>
            <strong className="font-mono font-bold">{Number(performance.score).toFixed(1)}</strong>
            <small className="text-[10px] opacity-80">{performance.level}</small>
          </span>
        </header>
        <div className="proc-score-bars flex flex-col gap-2 text-xs">
          <div className="flex items-center gap-2">
            <span className="w-24 text-[11px] text-text-muted flex-shrink-0">中标率得分</span>
            <div className="flex-1 bg-surface-subtle rounded-full h-2 overflow-hidden border border-border/40">
              <i className="block bg-accent h-full rounded-full transition-all" style={{ width: `${Math.min(100, Number(performance.win_rate_score))}%` }} />
            </div>
            <small className="font-mono font-semibold text-text w-8 text-right">{performance.win_rate_score}</small>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-24 text-[11px] text-text-muted flex-shrink-0">活跃度得分</span>
            <div className="flex-1 bg-surface-subtle rounded-full h-2 overflow-hidden border border-border/40">
              <i className="block bg-accent h-full rounded-full transition-all" style={{ width: `${Math.min(100, Number(performance.activity_score))}%` }} />
            </div>
            <small className="font-mono font-semibold text-text w-8 text-right">{performance.activity_score}</small>
          </div>
          <div className="flex items-center gap-2">
            <span className="w-24 text-[11px] text-text-muted flex-shrink-0">合作状态得分</span>
            <div className="flex-1 bg-surface-subtle rounded-full h-2 overflow-hidden border border-border/40">
              <i className="block bg-accent h-full rounded-full transition-all" style={{ width: `${Math.min(100, Number(performance.status_score))}%` }} />
            </div>
            <small className="font-mono font-semibold text-text w-8 text-right">{performance.status_score}</small>
          </div>
        </div>
        <p className="proc-eval-note text-[11px] text-text-muted leading-relaxed">口径：中标率满 60 分但报价少于 3 次按 0.5 折减；黑名单强制封顶 30 分。</p>
      </section>

      <section className="proc-report-section glass-panel bg-surface/80 p-4 rounded-xl border border-border/80 flex flex-col gap-3">
        <header className="flex items-center gap-2 pb-2 border-b border-border/40"><UserRound size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">档案信息</h3></header>
        <dl className="proc-facts-grid grid grid-cols-2 gap-3 text-xs">
          <div><dt className="text-[11px] text-text-muted">联系人</dt><dd className="font-medium text-text mt-0.5">{profile.contact_person || "—"}</dd></div>
          <div><dt className="text-[11px] text-text-muted">电话</dt><dd className="font-medium font-mono text-text mt-0.5">{profile.phone || "—"}</dd></div>
          <div><dt className="text-[11px] text-text-muted">邮箱</dt><dd className="font-medium font-mono text-text mt-0.5">{profile.email || "—"}</dd></div>
          <div><dt className="text-[11px] text-text-muted">地址</dt><dd className="font-medium text-text mt-0.5">{profile.address || "—"}</dd></div>
          <div><dt className="text-[11px] text-text-muted">合作状态</dt><dd className="font-medium text-text mt-0.5">{profile.cooperation_status}</dd></div>
          <div><dt className="text-[11px] text-text-muted">备注</dt><dd className="font-medium text-text mt-0.5">{profile.notes || "—"}</dd></div>
        </dl>
      </section>

      <section className="proc-report-section glass-panel bg-surface/80 p-4 rounded-xl border border-border/80 flex flex-col gap-3">
        <header className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-border/40">
          <div className="flex items-center gap-2"><Archive size={16} className="text-accent" /><h3 className="text-xs font-bold text-text">参与记录</h3></div>
          <span className="text-[11px] text-text-muted">{profile.quote_count} 次报价 · {profile.win_count} 次中标 · 中标率 {profile.win_rate}</span>
        </header>
        <div className="proc-supplier-metrics grid grid-cols-3 gap-2 text-center bg-surface-subtle p-3 rounded-lg border border-border/40">
          <div className="flex flex-col"><strong className="font-mono text-base font-bold text-text">{profile.quote_count}</strong><small className="text-[11px] text-text-muted">报价次数</small></div>
          <div className="flex flex-col"><strong className="font-mono text-base font-bold text-accent">{profile.win_count}</strong><small className="text-[11px] text-text-muted">中标次数</small></div>
          <div className="flex flex-col"><strong className="font-mono text-base font-bold text-text">{Number(profile.win_rate) * 100}%</strong><small className="text-[11px] text-text-muted">中标率</small></div>
        </div>
        <h4 className="text-xs font-bold text-text mt-1">参与物料</h4>
        <div className="proc-tag-row flex flex-wrap gap-1.5">
          {profile.items.length ? profile.items.map((item) => <span className="proc-tag text-[11px] font-medium px-2.5 py-1 rounded-md bg-surface-subtle border border-border text-text" key={item}>{item}</span>) : <span className="proc-muted text-xs text-text-muted">暂无</span>}
        </div>
        <h4 className="text-xs font-bold text-text mt-1">最近报价</h4>
        <ul className="proc-quote-list flex flex-col gap-2 text-xs">
          {profile.recent_quotes.length ? profile.recent_quotes.map((quote) => (
            <li className="p-2.5 rounded-lg border border-border/60 bg-surface/60 hover:bg-surface hover:border-accent/40 transition-all" key={quote.quote_id}>
              <button className="w-full text-left flex flex-col gap-0.5" type="button" onClick={() => onOpenTask(quote.task_id)} title="打开采购任务">
                <strong className="font-semibold text-text">{quote.item_name || "未知物料"}</strong>
                <small className="text-[11px] text-text-muted font-mono">{quote.task_reference || quote.task_id} · {quote.source_filename}</small>
              </button>
            </li>
          )) : <li className="proc-muted text-xs text-text-muted py-2">暂无报价记录</li>}
        </ul>
      </section>
    </div>
  );
}
