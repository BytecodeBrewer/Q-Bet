(() => {
  const links = document.querySelectorAll("[data-download-feedback]");
  if (!links.length) return;

  const status = document.querySelector("[data-download-status]");
  const setStatus = (message, state) => {
    if (!status) return;
    status.textContent = message;
    status.dataset.state = state;
  };

  const filenameFrom = (response, fallback) => {
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    return match ? match[1] : fallback;
  };

  links.forEach((link) => {
    link.addEventListener("click", async (event) => {
      event.preventDefault();
      setStatus("Preparing download…", "working");
      try {
        const response = await fetch(link.href, {
          credentials: "same-origin",
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        if (!response.ok) {
          const message = await response.text();
          throw new Error(message || ("Download failed with " + response.status));
        }
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        anchor.download = filenameFrom(response, "qbet-export");
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(objectUrl);
        const empty = response.headers.get("X-QBet-Export-State") === "no-data";
        setStatus(empty ? "Downloaded an empty report for this filter." : "Download ready.", "success");
      } catch (error) {
        setStatus(error.message || "Download failed.", "error");
      }
    });
  });
})();
