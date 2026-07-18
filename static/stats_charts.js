/*
 * stats_charts.js - shared Chart.js builders for the admin stats page and the
 * printable year report.
 *
 * Colours are the official Scouts brand palette (guidelines v3.2 July 2024).
 * Palette validated with the dataviz six-checks validator:
 *  - working set is purple/teal/orange only (Forest Green fails the lightness
 *    band + chroma floor, so it is not used in charts)
 *  - orange is low-contrast on white (2.25:1) which is permitted ONLY with
 *    visible labels/legend and an accompanying data table - both are present
 *    wherever orange appears (event-mix doughnut)
 *  - single-measure bar charts use one hue; identity lives on the axis labels
 */

const StatsCharts = (function () {
  "use strict";

  const PURPLE = "#7413dc"; // Scouts Purple (core)
  const TEAL = "#088486"; // Scouts Teal
  const ORANGE = "#ff912a"; // Scouts Orange
  const INK = "#212529"; // text stays in ink, never series colour
  const GRID = "rgba(0, 0, 0, 0.06)";

  const MONTH_LABELS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];

  const EVENT_LABELS = { day: "Day visits", eve: "Evening visits", overnight: "Overnight camps" };

  function pounds(pence) {
    return "£" + (pence / 100).toLocaleString("en-GB", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  let pixelRatio; // undefined = Chart.js default (window.devicePixelRatio)

  function baseOptions(animate) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      devicePixelRatio: pixelRatio,
      animation: animate ? {} : false,
      plugins: {
        legend: { display: false },
        tooltip: { titleColor: "#fff", bodyColor: "#fff" },
      },
    };
  }

  function axisDefaults() {
    return {
      grid: { color: GRID },
      border: { display: false },
      ticks: { color: INK },
    };
  }

  /* Monthly seasonality: single-series bars (people), bookings shown in the
     tooltip rather than on a second axis - dual axes mislead. */
  function monthlyChart(ctx, y, animate) {
    return new Chart(ctx, {
      type: "bar",
      data: {
        labels: MONTH_LABELS,
        datasets: [{
          label: "People on site",
          data: y.monthly_people,
          backgroundColor: PURPLE,
          borderRadius: 4,
          maxBarThickness: 34,
        }],
      },
      options: {
        ...baseOptions(animate),
        plugins: {
          ...baseOptions(animate).plugins,
          tooltip: {
            callbacks: {
              afterLabel: (item) =>
                y.monthly_bookings[item.dataIndex] + " booking(s)",
            },
          },
        },
        scales: { x: axisDefaults(), y: { ...axisDefaults(), beginAtZero: true } },
      },
    });
  }

  /* Event mix: 3 slices max so the doughnut stays readable; white 2px borders
     separate segments; legend + tooltips carry identity (orange relief). */
  function eventChart(ctx, y, animate) {
    const entries = Object.entries(y.event_people).filter(([, v]) => v > 0);
    return new Chart(ctx, {
      type: "doughnut",
      data: {
        labels: entries.map(([k]) => EVENT_LABELS[k] || k),
        datasets: [{
          data: entries.map(([, v]) => v),
          backgroundColor: [PURPLE, TEAL, ORANGE],
          borderColor: "#ffffff",
          borderWidth: 2,
        }],
      },
      options: {
        ...baseOptions(animate),
        plugins: {
          legend: { display: true, position: "bottom", labels: { color: INK } },
          tooltip: {
            callbacks: {
              label: (item) => " " + item.label + ": " + item.parsed + " people",
            },
          },
        },
      },
    });
  }

  /* Income by group type: one measure across categories - single teal hue,
     identity on the axis labels. */
  function incomeChart(ctx, y, animate) {
    const entries = Object.entries(y.income_by_group_type)
      .sort((a, b) => b[1] - a[1]);
    return new Chart(ctx, {
      type: "bar",
      data: {
        labels: entries.map(([k]) => k),
        datasets: [{
          label: "Estimated income",
          data: entries.map(([, v]) => v),
          backgroundColor: TEAL,
          borderRadius: 4,
          maxBarThickness: 26,
        }],
      },
      options: {
        ...baseOptions(animate),
        indexAxis: "y",
        plugins: {
          ...baseOptions(animate).plugins,
          tooltip: { callbacks: { label: (item) => " " + pounds(item.parsed.x) } },
        },
        scales: {
          x: {
            ...axisDefaults(),
            beginAtZero: true,
            ticks: { color: INK, callback: (v) => pounds(v) },
          },
          y: { ...axisDefaults(), grid: { display: false } },
        },
      },
    });
  }

  /* Facility popularity: one measure across categories - single purple hue. */
  function facilitiesChart(ctx, y, animate) {
    return new Chart(ctx, {
      type: "bar",
      data: {
        labels: y.facility_counts.map((f) => f[0]),
        datasets: [{
          label: "Bookings using facility",
          data: y.facility_counts.map((f) => f[1]),
          backgroundColor: PURPLE,
          borderRadius: 4,
          maxBarThickness: 26,
        }],
      },
      options: {
        ...baseOptions(animate),
        indexAxis: "y",
        scales: {
          x: { ...axisDefaults(), beginAtZero: true, ticks: { color: INK, precision: 0 } },
          y: { ...axisDefaults(), grid: { display: false } },
        },
      },
    });
  }

  /* Build the four charts for one year. Canvas id convention:
     chart-monthly-<year>, chart-events-<year>, chart-income-<year>,
     chart-facilities-<year>. Missing canvases are skipped. */
  function renderYearCharts(y, opts) {
    const animate = !opts || opts.animate !== false;
    pixelRatio = opts && opts.dpr ? opts.dpr : undefined;
    const builders = {
      monthly: monthlyChart,
      events: eventChart,
      income: incomeChart,
      facilities: facilitiesChart,
    };
    const charts = [];
    for (const [name, build] of Object.entries(builders)) {
      const canvas = document.getElementById("chart-" + name + "-" + y.year);
      if (canvas) {
        charts.push(build(canvas.getContext("2d"), y, animate));
      }
    }
    return charts;
  }

  /* Admin page: charts inside hidden tab panes initialise at 0x0, so render
     the active year now and each other year once, when its tab first shows. */
  function initAdminTabs(yearStats) {
    const rendered = new Set();

    function renderYear(year) {
      if (rendered.has(year)) return;
      const y = yearStats.find((s) => s.year === year);
      if (y) {
        rendered.add(year);
        renderYearCharts(y);
      }
    }

    const activePane = document.querySelector(".tab-pane.active[data-year]");
    if (activePane) {
      renderYear(parseInt(activePane.dataset.year, 10));
    }

    document.querySelectorAll('[data-bs-toggle="tab"]').forEach((tabEl) => {
      tabEl.addEventListener("shown.bs.tab", (event) => {
        const pane = document.querySelector(event.target.dataset.bsTarget);
        if (pane && pane.dataset.year) {
          renderYear(parseInt(pane.dataset.year, 10));
        }
      });
    });
  }

  return { renderYearCharts, initAdminTabs, pounds };
})();
