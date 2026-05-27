const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function toast(message) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  el.classList.add("show");
  setTimeout(() => el.classList.remove("show"), 2200);
}

async function readJson(response) {
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.message || `请求失败: ${response.status}`);
  }
  return payload;
}

async function postJson(url, body = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  return readJson(response);
}

async function getJson(url) {
  const response = await fetch(url);
  return readJson(response);
}

function ensureChart(id) {
  const el = document.getElementById(id);
  if (!el || !window.echarts) return null;
  return echarts.getInstanceByDom(el) || echarts.init(el);
}

function bindChartResize(id, chart) {
  const el = document.getElementById(id);
  if (!el || el.dataset.resizeBound) return;
  window.addEventListener("resize", () => chart.resize());
  el.dataset.resizeBound = "1";
}

function initLineChart(id, labels, values, name) {
  const chart = ensureChart(id);
  if (!chart) return;
  chart.setOption({
    tooltip: {trigger: "axis"},
    grid: {top: 28, left: 42, right: 20, bottom: 34},
    xAxis: {type: "category", data: labels},
    yAxis: {type: "value", splitLine: {lineStyle: {color: "#edf2ee"}}},
    series: [{
      name,
      type: "line",
      smooth: true,
      showSymbol: false,
      areaStyle: {opacity: 0.16},
      lineStyle: {width: 3, color: "#0f8f78"},
      itemStyle: {color: "#0f8f78"},
      data: values,
    }],
  });
  bindChartResize(id, chart);
}

function initBarChart(id, labels, values, name) {
  const chart = ensureChart(id);
  if (!chart) return;
  chart.setOption({
    tooltip: {trigger: "axis"},
    grid: {top: 28, left: 42, right: 20, bottom: 54},
    xAxis: {type: "category", data: labels, axisLabel: {rotate: 30}},
    yAxis: {type: "value"},
    series: [{name, type: "bar", data: values, itemStyle: {color: "#c98215", borderRadius: [6, 6, 0, 0]}}],
  });
  bindChartResize(id, chart);
}

function initFinanceChart(id, rows) {
  const chart = ensureChart(id);
  if (!chart) return;
  chart.setOption({
    tooltip: {trigger: "axis"},
    legend: {top: 0, right: 0},
    grid: {top: 42, left: 52, right: 28, bottom: 42},
    xAxis: {type: "category", data: rows.map(item => item.month)},
    yAxis: {type: "value"},
    series: [
      {name: "收入", type: "bar", data: rows.map(item => item.revenue), itemStyle: {color: "#0f8f78", borderRadius: [6, 6, 0, 0]}},
      {name: "利润", type: "line", smooth: true, data: rows.map(item => item.profit), lineStyle: {width: 3, color: "#2d6cdf"}, itemStyle: {color: "#2d6cdf"}},
    ],
  });
  bindChartResize(id, chart);
}

function bindSearch() {
  $$("[data-search]").forEach(input => {
    const target = input.dataset.search;
    input.addEventListener("input", () => {
      const keyword = input.value.trim();
      $$(target).forEach(card => {
        const text = card.dataset.name || card.textContent;
        card.style.display = !keyword || text.includes(keyword) ? "" : "none";
      });
    });
  });
}

function bindFilters() {
  $$("[data-filter]").forEach(btn => {
    btn.addEventListener("click", () => {
      $$("[data-filter]").forEach(item => item.classList.remove("active"));
      btn.classList.add("active");
      const filter = btn.dataset.filter;
      $$(".pond-card[data-risk]").forEach(card => {
        card.style.display = filter === "all" || card.dataset.risk === filter ? "" : "none";
      });
    });
  });

  $$("[data-alert-filter]").forEach(btn => {
    btn.addEventListener("click", () => {
      $$("[data-alert-filter]").forEach(item => item.classList.remove("active"));
      btn.classList.add("active");
      const filter = btn.dataset.alertFilter;
      $$(".alarm-row[data-status]").forEach(row => {
        row.style.display = !filter || row.dataset.status === filter ? "" : "none";
      });
    });
  });

  $$("[data-order-filter]").forEach(btn => {
    btn.addEventListener("click", () => {
      $$("[data-order-filter]").forEach(item => item.classList.remove("active"));
      btn.classList.add("active");
      const filter = btn.dataset.orderFilter;
      $$(".order-table [data-status]").forEach(row => {
        row.style.display = !filter || row.dataset.status === filter ? "" : "none";
      });
    });
  });

  $$("[data-product-filter]").forEach(btn => {
    btn.addEventListener("click", () => {
      $$("[data-product-filter]").forEach(item => item.classList.remove("active"));
      btn.classList.add("active");
      const filter = btn.dataset.productFilter;
      $$(".product-card[data-stock]").forEach(card => {
        card.style.display = filter === "all" || card.dataset.stock === filter ? "" : "none";
      });
    });
  });

  $("[data-device-filter]")?.addEventListener("change", event => {
    const type = event.target.value;
    $$(".device-card[data-type]").forEach(card => {
      card.style.display = !type || card.dataset.type === type ? "" : "none";
    });
  });
}

function bindActions() {
  $("#refreshBtn")?.addEventListener("click", () => location.reload());
  $$("[data-demo-toast]").forEach(btn => btn.addEventListener("click", () => toast(btn.dataset.demoToast)));
  $("[data-batch-command]")?.addEventListener("click", () => toast("已生成批量巡检指令，可在设备日志中查看闭环记录"));

  $$("[data-device-command]").forEach(btn => {
    btn.addEventListener("click", async () => {
      try {
        const result = await postJson(`/api/devices/${btn.dataset.deviceCommand}/command`, {action: btn.dataset.action});
        toast(result.message || (result.ok ? "UDP 指令已发出" : "设备指令失败"));
        if (result.ok) setTimeout(() => location.reload(), 500);
      } catch (error) {
        toast(error.message);
      }
    });
  });

  $$("[data-resolve-alert]").forEach(btn => {
    btn.addEventListener("click", async () => {
      try {
        const result = await postJson(`/api/alerts/${btn.dataset.resolveAlert}/resolve`);
        toast(result.ok ? "告警已标记处理" : "告警处理失败");
        if (result.ok) setTimeout(() => location.reload(), 500);
      } catch (error) {
        toast(error.message);
      }
    });
  });

  $$("[data-threshold-form]").forEach(form => {
    form.addEventListener("submit", async event => {
      event.preventDefault();
      try {
        const data = Object.fromEntries(new FormData(form).entries());
        const result = await postJson("/api/thresholds", data);
        toast(result.ok ? "阈值已保存到数据库" : "阈值保存失败");
      } catch (error) {
        toast(error.message);
      }
    });
  });

  $$("[data-inventory-form]").forEach(form => {
    form.addEventListener("submit", async event => {
      event.preventDefault();
      try {
        const data = Object.fromEntries(new FormData(form).entries());
        const result = await postJson(`/api/products/${form.dataset.productId}/inventory`, data);
        toast(result.ok ? "库存已更新" : "库存更新失败");
      } catch (error) {
        toast(error.message);
      }
    });
  });

  $$("[data-order-status]").forEach(btn => {
    btn.addEventListener("click", async () => {
      try {
        const result = await postJson(`/api/orders/${btn.dataset.orderStatus}/status`, {status: btn.dataset.nextStatus});
        toast(result.ok ? "订单状态已推进" : "订单更新失败");
        if (result.ok) setTimeout(() => location.reload(), 500);
      } catch (error) {
        toast(error.message);
      }
    });
  });
}

function renderHardwareStatus(status) {
  if (!status) return;
  const lastSender = status.last_sender_ip ? `${status.last_sender_ip}:${status.last_sender_port ?? "--"}` : "--";
  const fields = {
    running_label: status.running ? "运行中" : "已停止",
    mode: status.mode ? String(status.mode).toUpperCase() : "UDP",
    bind_host: status.bind_host || "--",
    bind_port: status.bind_port ?? "--",
    command_port: status.command_port ?? "--",
    encoding: status.encoding || "--",
    total_packets: status.total_packets ?? 0,
    parsed_packets: status.parsed_packets ?? 0,
    error_count: status.error_count ?? 0,
    enabled_ponds: status.enabled_ponds ?? 0,
    last_sender: lastSender,
    last_packet_time: status.last_packet_time || "--",
    last_text_preview: status.last_text_preview || "等待硬件数据",
    last_hex_preview: status.last_hex_preview || "暂无",
    last_error: status.last_error || "无",
    image_packets: status.image_packets ?? 0,
    image_completed: status.image_completed ?? 0,
    image_failed: status.image_failed ?? 0,
    image_sessions: status.image_sessions ?? 0,
    image_bytes_received: status.image_bytes_received ?? 0,
    last_image_time: status.last_image_time || "--",
    last_image_id: status.last_image_id || "--",
    last_image_error: status.last_image_error || "无",
  };
  Object.entries(fields).forEach(([key, value]) => {
    const node = document.querySelector(`[data-hardware-field="${key}"]`);
    if (node) node.textContent = String(value);
  });
  const badge = $("[data-hardware-state-badge]");
  if (badge) {
    badge.textContent = status.running ? "运行中" : "未启动";
    badge.className = `status ${status.running ? "running" : "offline"}`;
  }
  const startButton = $("#hardwareStartBtn");
  const stopButton = $("#hardwareStopBtn");
  if (startButton) startButton.disabled = !!status.running || status.supported === false;
  if (stopButton) stopButton.disabled = !status.running;
}

function renderAIModelStatus(model) {
  if (!model) return;
  const fields = {
    model_ready: model.class_model_exists ? "就绪" : "待训练",
    ultralytics_status: model.ultralytics_available ? "可用" : "未安装",
    class_model_exists: model.class_model_exists ? "已加载" : "缺失",
    detect_model_exists: model.detect_model_exists ? "已加载" : "缺失",
    class_model_path: model.class_model_path || "--",
    detect_model_path: model.detect_model_path || "--",
    upload_root: model.upload_root || "--",
  };
  Object.entries(fields).forEach(([key, value]) => {
    const node = document.querySelector(`[data-ai-field="${key}"]`);
    if (node) node.textContent = String(value);
  });
}

function updateHardwareToggleLabel(input) {
  const label = input.closest(".hardware-toggle")?.querySelector("[data-toggle-label]");
  if (label) {
    label.textContent = input.checked ? "已接收硬件数据" : "未接收硬件数据";
  }
}

function bindHardware() {
  const page = document.body.dataset.page;
  if (!["admin_ponds", "admin_ai"].includes(page)) return;

  let pollingHandle = null;
  const applyStatus = payload => {
    const status = payload?.hardware || payload;
    renderHardwareStatus(status);
    if (payload?.model) renderAIModelStatus(payload.model);
    return status;
  };
  const refreshStatus = async (quiet = false) => {
    try {
      const payload = await getJson(page === "admin_ai" ? "/api/ai/status" : "/api/hardware/status");
      return applyStatus(payload);
    } catch (error) {
      if (!quiet) toast(error.message);
      return null;
    }
  };

  renderHardwareStatus(window.hardwareStatus);
  renderAIModelStatus(window.aiModelStatus);
  refreshStatus(true);
  pollingHandle = window.setInterval(() => refreshStatus(true), 5000);
  window.addEventListener("beforeunload", () => pollingHandle && window.clearInterval(pollingHandle), {once: true});

  $("#hardwareStartBtn")?.addEventListener("click", async () => {
    try {
      const result = await postJson("/api/hardware/start");
      toast(result.message || "UDP 接收已启动");
      await refreshStatus(true);
    } catch (error) {
      toast(error.message);
    }
  });

  $("#hardwareStopBtn")?.addEventListener("click", async () => {
    try {
      const result = await postJson("/api/hardware/stop");
      toast(result.message || "UDP 接收已停止");
      await refreshStatus(true);
    } catch (error) {
      toast(error.message);
    }
  });

  $$("[data-hardware-toggle]").forEach(input => {
    updateHardwareToggleLabel(input);
    input.addEventListener("change", async () => {
      input.disabled = true;
      try {
        const result = await postJson(`/api/ponds/${input.dataset.hardwareToggle}/hardware-ingest`, {enabled: input.checked});
        if (!result.ok) {
          input.checked = !input.checked;
          toast(result.message || "鱼池接收开关更新失败");
        } else {
          updateHardwareToggleLabel(input);
          toast(`${result.pond_name}${input.checked ? "已开启" : "已关闭"}硬件接收`);
          await refreshStatus(true);
        }
      } catch (error) {
        input.checked = !input.checked;
        toast(error.message);
      } finally {
        input.disabled = false;
        updateHardwareToggleLabel(input);
      }
    });
  });
}

function bindCharts() {
  if (window.pageCharts?.temperatureChart) {
    const data = window.pageCharts.temperatureChart;
    initLineChart("temperatureChart", data.labels, data.values, data.title);
  }
  if (window.pondTrends) {
    const select = $("#pondMetric");
    const render = () => {
      const trend = window.pondTrends[select.value];
      const title = select.selectedOptions[0].textContent;
      initLineChart("pondTrendChart", trend.labels, trend.values, title);
    };
    select?.addEventListener("change", render);
    render();
  }
  if (window.analyticsData) {
    const select = $("#analyticsMetric");
    const render = () => {
      const trend = window.analyticsData.trend[select.value];
      initLineChart("metricCompareChart", trend.labels, trend.values, select.selectedOptions[0].textContent);
    };
    select?.addEventListener("change", render);
    render();
    if (window.echarts && $("#riskChart")) {
      const risk = ensureChart("riskChart");
      risk?.setOption({tooltip: {trigger: "item"}, series: [{type: "pie", radius: ["48%", "72%"], data: window.analyticsData.risk_distribution}]});
    }
    if (window.analyticsData.energy) {
      initBarChart("energyChart", window.analyticsData.energy.map(item => item.name), window.analyticsData.energy.map(item => item.value), "能耗");
    }
  }
  if (window.supplierSales) {
    initLineChart("supplierSalesChart", window.supplierSales.map(item => item.month), window.supplierSales.map(item => item.revenue), "销售额");
  }
  if (window.financeData?.series) {
    initFinanceChart("financeChart", window.financeData.series);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  bindSearch();
  bindFilters();
  bindActions();
  bindCharts();
  bindHardware();
});
