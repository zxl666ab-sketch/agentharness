import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { LoaderCircle } from "lucide-react";

import { cn } from "../../lib/utils";

export type ButtonVariant = "primary" | "secondary" | "danger" | "warning" | "ghost" | "plain";
export type ButtonSize = "sm" | "md";

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: ReactNode;
  /** 传入即进入加载态：图标替换为 spinner 并禁用。 */
  loading?: boolean;
};

/** 全站统一按钮（Phase 3）：外观完全由 .proc-button 变体类控制。 */
export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  {
    variant = "secondary",
    size = "md",
    icon,
    loading = false,
    className,
    children,
    disabled,
    type = "button",
    ...rest
  },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cn("proc-button", `is-${variant}`, size === "sm" ? "is-sm" : "is-md", className)}
      disabled={disabled || loading}
      {...rest}
    >
      {loading ? <LoaderCircle className="spin" size={size === "sm" ? 14 : 15} aria-hidden /> : icon}
      {children ? <span>{children}</span> : null}
    </button>
  );
});

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  tone?: "default" | "danger";
  children: ReactNode;
};

/** 图标按钮：label 进入 aria-label 与 title，保证可发现性。 */
export function IconButton({ label, tone = "default", className, children, type = "button", ...rest }: IconButtonProps) {
  return (
    <button
      type={type}
      className={cn("proc-icon-button", tone === "danger" && "danger-hover", className)}
      title={label}
      aria-label={label}
      {...rest}
    >
      {children}
    </button>
  );
}
