import React from "react";
import IconButton from "./IconButton";

interface CopyToClipboardButtonProps {
  text: string;
  title?: string;
  ariaLabel?: string;
  disabled?: boolean;
}

const CopyGlyph: React.FC<{ copied?: boolean }> = ({ copied = false }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path
      d="M8 3H19C20.1046 3 21 3.89543 21 5V16"
      stroke={copied ? "#107c10" : "currentColor"}
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <rect
      x="3"
      y="8"
      width="14"
      height="13"
      rx="1.5"
      stroke={copied ? "#107c10" : "currentColor"}
      strokeWidth="1.8"
    />
    <path
      d="M7 12H13"
      stroke={copied ? "#107c10" : "currentColor"}
      strokeWidth="1.6"
      strokeLinecap="round"
    />
    <path
      d="M7 15H13"
      stroke={copied ? "#107c10" : "currentColor"}
      strokeWidth="1.6"
      strokeLinecap="round"
    />
    <path
      d="M7 18H12"
      stroke={copied ? "#107c10" : "currentColor"}
      strokeWidth="1.6"
      strokeLinecap="round"
    />
  </svg>
);

const CopyToClipboardButton: React.FC<CopyToClipboardButtonProps> = ({
  text,
  title = "Copy",
  ariaLabel,
  disabled = false,
}) => {
  const [copied, setCopied] = React.useState(false);
  const timeoutRef = React.useRef<number | null>(null);

  React.useEffect(() => {
    return () => {
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
    };
  }, []);

  const handleCopy = React.useCallback(async () => {
    if (!text.trim()) return;

    const fallbackCopy = () => {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "absolute";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
    };

    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        fallbackCopy();
      }

      setCopied(true);
      if (timeoutRef.current !== null) {
        window.clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = window.setTimeout(() => setCopied(false), 1500);
    } catch {
      try {
        fallbackCopy();
        setCopied(true);
        if (timeoutRef.current !== null) {
          window.clearTimeout(timeoutRef.current);
        }
        timeoutRef.current = window.setTimeout(() => setCopied(false), 1500);
      } catch {
      }
    }
  }, [text]);

  return (
    <IconButton
      onClick={handleCopy}
      title={copied ? "Copied" : title}
      ariaLabel={ariaLabel ?? title}
      disabled={disabled || !text.trim()}
    >
      <CopyGlyph copied={copied} />
    </IconButton>
  );
};

export default CopyToClipboardButton;
