// Auto-dismiss flash alerts after 5 seconds
document.addEventListener("DOMContentLoaded", () => {
    setTimeout(() => {
      document.querySelectorAll(".alert").forEach((alert) => {
        const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
        bsAlert.close();
      });
    }, 5000);
  });
