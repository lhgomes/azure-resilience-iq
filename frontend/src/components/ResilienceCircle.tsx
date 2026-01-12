import React from "react";

interface ResilienceCircleProps {
  passedChecks: number;
  failedChecks: number;
  size?: number; // diameter in pixels, default 80
  children?: React.ReactNode; // Icon or other content to display in center
}

/**
 * Renders a circular progress indicator showing resilience check results.
 * The circle is divided into passed (green) and failed (red) sections,
 * with the pass percentage displayed at the bottom.
 */
const ResilienceCircle: React.FC<ResilienceCircleProps> = ({
  passedChecks,
  failedChecks,
  size = 80,
  children,
}) => {
  const totalChecks = passedChecks + failedChecks;
  
  // Handle edge case: no checks available - show gray circle with icon
  if (totalChecks === 0) {
    return (
      <div
        style={{
          position: "relative",
          width: `${size}px`,
          height: `${size}px`,
          borderRadius: "50%",
          background: "#e5e7eb",
          padding: "4px",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
        }}
        title="No checks available"
      >
        {/* Inner white circle */}
        <div
          style={{
            width: "100%",
            height: "100%",
            borderRadius: "50%",
            background: "#ffffff",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            flexDirection: "column",
            position: "relative",
          }}
        >
          {/* Center content (icon) */}
          {children && (
            <div style={{
              position: "absolute",
              top: "50%",
              left: "50%",
              transform: "translate(-50%, -50%)",
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
            }}>
              {children}
            </div>
          )}
        </div>
      </div>
    );
  }

  const passPercentage = (passedChecks / totalChecks) * 100;
  const failPercentage = (failedChecks / totalChecks) * 100;

  // Create conic gradient: green for passed, red for failed
  const conicGradient = `conic-gradient(
    from 0deg,
    #10b981 0deg ${(passPercentage / 100) * 360}deg,
    #ef4444 ${(passPercentage / 100) * 360}deg 360deg
  )`;

  return (
    <div
      style={{
        position: "relative",
        width: `${size}px`,
        height: `${size}px`,
        borderRadius: "50%",
        background: conicGradient,
        padding: "4px",
        display: "flex",
        justifyContent: "center",
        alignItems: "center",
        flexDirection: "column",
      }}
      title={`${passedChecks} passed, ${failedChecks} failed`}
    >
      {/* Inner white circle to create ring effect */}
      <div
        style={{
          width: "100%",
          height: "100%",
          borderRadius: "50%",
          background: "#ffffff",
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          flexDirection: "column",
          position: "relative",
        }}
      >
        {/* Center content (icon) */}
        {children && (
          <div style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
          }}>
            {children}
          </div>
        )}
        
        {/* Pass percentage text at bottom */}
        <span
          style={{
            fontSize: "10px",
            fontWeight: 700,
            color: "#10b981",
            lineHeight: "1",
            letterSpacing: "-0.5px",
            marginTop: "auto",
            paddingBottom: "4px",
          }}
        >
          {passPercentage.toFixed(0)}%
        </span>
      </div>
    </div>
  );
};

export default ResilienceCircle;
