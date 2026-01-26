import React, { useState, useEffect } from "react";
import TerraformUpload from "./TerraformUpload";
import "./SubscriptionPicker.css";

export interface SubscriptionInfo {
  id: string;
  name: string;
}

interface SubscriptionPickerProps {
  subscriptions: SubscriptionInfo[];
  onSelect: (subscriptionId: string) => void;
  onRefresh: () => void;
  loading?: boolean;
}

const SubscriptionPicker: React.FC<SubscriptionPickerProps> = ({
  subscriptions,
  onSelect,
  onRefresh,
  loading = false,
}) => {
  const [showTerraformUpload, setShowTerraformUpload] = useState(false);

  const handleTerraformUploadComplete = (subscriptionId: string, subscriptionName: string) => {
    setShowTerraformUpload(false);
    // Refresh subscriptions list and select the new one
    setTimeout(() => {
      onRefresh();
      onSelect(subscriptionId);
    }, 500);
  };

  return (
    <>
      <div className="subscription-picker-overlay">
        <div className="subscription-picker-container">
          <div className="picker-header">
            <h1>Azure Resiliency IQ</h1>
            <p>Select a subscription to analyze</p>
          </div>

          <div className="picker-content">
            {loading ? (
              <div className="loading-state">
                <div className="spinner" />
                <p>Loading subscriptions...</p>
              </div>
            ) : subscriptions.length === 0 ? (
              <div className="empty-state">
                <p>No subscriptions available</p>
                <button
                  className="btn-refresh"
                  onClick={onRefresh}
                >
                  Refresh
                </button>
              </div>
            ) : (
              <div className="subscriptions-grid">
                {subscriptions.map((sub) => (
                  <button
                    key={sub.id}
                    className="subscription-card"
                    onClick={() => onSelect(sub.id)}
                  >
                    <div className="card-icon">☁️</div>
                    <div className="card-content">
                      <div className="card-name">{sub.name}</div>
                      <div className="card-id">{sub.id}</div>
                    </div>
                    <div className="card-arrow">→</div>
                  </button>
                ))}
              </div>
            )}
          </div>

          <div className="picker-footer">
            <button
              className="btn-terraform"
              onClick={() => setShowTerraformUpload(true)}
            >
              📦 Import Terraform Configuration
            </button>
          </div>
        </div>
      </div>

      {showTerraformUpload && (
        <TerraformUpload
          onUploadComplete={handleTerraformUploadComplete}
          onCancel={() => setShowTerraformUpload(false)}
        />
      )}
    </>
  );
};

export default SubscriptionPicker;
