import React from "react";
import IconButton from "./IconButton";

interface CloseIconButtonProps {
  onClick?: () => void;
  title?: string;
  ariaLabel?: string;
  absolute?: boolean;
  top?: number;
  right?: number;
  disabled?: boolean;
}

const CloseIconButton: React.FC<CloseIconButtonProps> = ({
  onClick,
  title = "Close",
  ariaLabel,
  absolute = false,
  top = 8,
  right = 8,
  disabled = false,
}) => (
  <IconButton
    onClick={onClick}
    title={title}
    ariaLabel={ariaLabel}
    disabled={disabled}
    style={
      absolute
        ? {
            position: "absolute",
            top,
            right,
          }
        : undefined
    }
  >
    ✕
  </IconButton>
);

export default CloseIconButton;
