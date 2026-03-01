import React from "react";
import "./IconButton.css";

type IconButtonVariant = "default" | "primary" | "danger" | "success";

interface IconButtonProps {
  onClick?: () => void;
  title?: string;
  ariaLabel?: string;
  disabled?: boolean;
  type?: "button" | "submit" | "reset";
  variant?: IconButtonVariant;
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}

const IconButton: React.FC<IconButtonProps> = ({
  onClick,
  title,
  ariaLabel,
  disabled = false,
  type = "button",
  variant = "default",
  className,
  style,
  children,
}) => {
  const variantClass =
    variant === "primary"
      ? "shared-icon-button--primary"
      : variant === "danger"
        ? "shared-icon-button--danger"
        : variant === "success"
          ? "shared-icon-button--success"
        : "";

  const classes = ["shared-icon-button", variantClass, className]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      type={type}
      onClick={onClick}
      title={title}
      aria-label={ariaLabel ?? title}
      disabled={disabled}
      className={classes}
      style={style}
    >
      {children}
    </button>
  );
};

export default IconButton;
