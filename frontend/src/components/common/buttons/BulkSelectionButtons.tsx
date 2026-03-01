import React from "react";
import { CheckmarkRegular, DismissRegular } from "@fluentui/react-icons";
import IconButton from "./IconButton";

interface BulkSelectionButtonsProps {
  onSelectAll: () => void;
  onUncheckAll: () => void;
  disabled?: boolean;
}

const BulkSelectionButtons: React.FC<BulkSelectionButtonsProps> = ({
  onSelectAll,
  onUncheckAll,
  disabled = false,
}) => (
  <div style={{ display: "flex", gap: 4 }}>
    <IconButton
      onClick={onSelectAll}
      title="Select All"
      ariaLabel="Select all"
      disabled={disabled}
      variant="primary"
    >
      <CheckmarkRegular style={{ fontSize: 16 }} />
    </IconButton>
    <IconButton
      onClick={onUncheckAll}
      title="Uncheck All"
      ariaLabel="Uncheck all"
      disabled={disabled}
      variant="danger"
    >
      <DismissRegular style={{ fontSize: 16 }} />
    </IconButton>
  </div>
);

export default BulkSelectionButtons;
