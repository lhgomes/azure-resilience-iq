import React, { useRef, useState } from "react";
import "./TerraformUpload.css";

interface TerraformUploadProps {
  onUploadComplete: (subscriptionId: string, subscriptionName: string) => void;
  onCancel: () => void;
}

const TerraformUpload: React.FC<TerraformUploadProps> = ({ onUploadComplete, onCancel }) => {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [subscriptionName, setSubscriptionName] = useState("Terraform");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    setSelectedFiles(files);
    setError(null);
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    const files = Array.from(e.dataTransfer.files);
    const tfFiles = files.filter(f => f.name.endsWith('.tf') || f.name.endsWith('.json'));
    
    if (tfFiles.length === 0) {
      setError('Please drop Terraform .tf or .json files');
      return;
    }
    
    setSelectedFiles(tfFiles);
    setError(null);
  };

  const handleUpload = async () => {
    if (selectedFiles.length === 0) {
      setError('Please select Terraform files');
      return;
    }

    setLoading(true);
    setError(null);
    setProgress(0);

    try {
      const formData = new FormData();
      selectedFiles.forEach(file => {
        formData.append('files', file);
      });
      formData.append('subscription_name', subscriptionName);

      const response = await fetch('/api/terraform/upload', {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.detail || 'Upload failed');
      }

      const data = await response.json();
      setProgress(100);
      
      setTimeout(() => {
        onUploadComplete(data.subscription_id, data.subscription_name);
      }, 500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
      setLoading(false);
    }
  };

  return (
    <div className="terraform-upload-overlay">
      <div className="terraform-upload-modal">
        <div className="modal-header">
          <h2>Import Terraform Configuration</h2>
          <button className="close-btn" onClick={onCancel}>×</button>
        </div>

        <div className="modal-content">
          <div className="upload-section">
            <label>Subscription Name</label>
            <input
              type="text"
              value={subscriptionName}
              onChange={(e) => setSubscriptionName(e.target.value)}
              placeholder="e.g., My Terraform Deployment"
              disabled={loading}
            />
          </div>

          <div
            className="dropzone"
            onDrop={handleDrop}
            onDragOver={(e) => e.preventDefault()}
            onClick={() => fileInputRef.current?.click()}
          >
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".tf,.json"
              onChange={handleFileSelect}
              style={{ display: 'none' }}
              disabled={loading}
            />
            <div className="dropzone-content">
              <div className="icon">📄</div>
              <p>Drag & drop Terraform files here</p>
              <p className="subtitle">or click to select .tf and .json files</p>
            </div>
          </div>

          {selectedFiles.length > 0 && (
            <div className="files-list">
              <h4>Selected Files ({selectedFiles.length})</h4>
              <ul>
                {selectedFiles.map((file, idx) => (
                  <li key={idx}>{file.name}</li>
                ))}
              </ul>
            </div>
          )}

          {error && <div className="error-message">{error}</div>}

          {loading && (
            <div className="progress-bar">
              <div className="progress-fill" style={{ width: `${progress}%` }} />
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onCancel} disabled={loading}>
            Cancel
          </button>
          <button
            className="btn-primary"
            onClick={handleUpload}
            disabled={loading || selectedFiles.length === 0}
          >
            {loading ? 'Uploading...' : 'Upload & Analyze'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default TerraformUpload;
