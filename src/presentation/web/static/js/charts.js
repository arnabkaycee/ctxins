/**
 * Chart.js wrapper for token composition and prompt cache hit ratio.
 */
class DashboardCharts {
  /**
   * @param {string} canvasId
   * @param {function(number): void} onTurnSelect - Callback when user clicks on a turn.
   */
  constructor(canvasId, onTurnSelect) {
    this.canvas = document.getElementById(canvasId);
    this.onTurnSelect = onTurnSelect || (() => {});
    this.chart = null;
    this.turns = [];

    this._initChart();
  }

  /**
   * Initialize Chart.js stacked bar chart.
   * @private
   */
  _initChart() {
    if (!this.canvas) {
      console.warn('[DashboardCharts] Canvas element not found.');
      return;
    }

    if (typeof window.Chart === 'undefined') {
      console.warn('[DashboardCharts] Chart.js not loaded. Visual graphs disabled.');
      const ctx = this.canvas.getContext('2d');
      if (ctx) {
        ctx.fillStyle = '#8b949e';
        ctx.font = '14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText('Chart library offline or loading...', this.canvas.width / 2, this.canvas.height / 2);
      }
      return;
    }

    const ctx = this.canvas.getContext('2d');

    this.chart = new window.Chart(ctx, {
      type: 'bar',
      data: {
        labels: [],
        datasets: [
          {
            label: 'System',
            data: [],
            backgroundColor: 'rgba(88, 166, 255, 0.85)',
            stack: 'tokens',
            yAxisID: 'y',
          },
          {
            label: 'Tools',
            data: [],
            backgroundColor: 'rgba(210, 153, 34, 0.85)',
            stack: 'tokens',
            yAxisID: 'y',
          },
          {
            label: 'History',
            data: [],
            backgroundColor: 'rgba(163, 113, 247, 0.85)',
            stack: 'tokens',
            yAxisID: 'y',
          },
          {
            label: 'Results',
            data: [],
            backgroundColor: 'rgba(248, 81, 73, 0.85)',
            stack: 'tokens',
            yAxisID: 'y',
          },
          {
            label: 'Thoughts',
            data: [],
            backgroundColor: 'rgba(110, 118, 129, 0.85)',
            stack: 'tokens',
            yAxisID: 'y',
          },
          {
            label: 'Output',
            data: [],
            backgroundColor: 'rgba(63, 185, 80, 0.85)',
            stack: 'tokens',
            yAxisID: 'y',
          },
          {
            label: 'Cache Hit %',
            type: 'line',
            data: [],
            borderColor: '#58a6ff',
            backgroundColor: 'rgba(88, 166, 255, 0.2)',
            borderWidth: 2,
            pointBackgroundColor: '#58a6ff',
            pointRadius: 4,
            tension: 0.2,
            yAxisID: 'y1',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: 'index',
          intersect: false,
        },
        onClick: (evt, elements) => {
          if (elements && elements.length > 0) {
            const clickedIndex = elements[0].index;
            if (this.turns && this.turns[clickedIndex] !== undefined) {
              const turnObj = this.turns[clickedIndex];
              const turnIdx = turnObj.turn_index !== undefined ? turnObj.turn_index : clickedIndex;
              this.onTurnSelect(turnIdx);
            }
          }
        },
        plugins: {
          legend: {
            position: 'top',
            labels: {
              color: '#c9d1d9',
              boxWidth: 12,
              font: {
                family: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
                size: 11,
              },
            },
          },
          tooltip: {
            backgroundColor: '#161b22',
            titleColor: '#f0f6fc',
            bodyColor: '#c9d1d9',
            borderColor: '#30363d',
            borderWidth: 1,
            padding: 10,
            callbacks: {
              footer: (items) => {
                let totalTokens = 0;
                items.forEach((item) => {
                  if (item.dataset.stack === 'tokens') {
                    totalTokens += Number(item.raw) || 0;
                  }
                });
                return `Total Prompt/Output: ${totalTokens.toLocaleString()} tokens`;
              },
            },
          },
        },
        scales: {
          x: {
            stacked: true,
            grid: {
              color: '#21262d',
            },
            ticks: {
              color: '#8b949e',
              font: {
                family: 'ui-monospace, monospace',
                size: 11,
              },
            },
          },
          y: {
            stacked: true,
            type: 'linear',
            position: 'left',
            title: {
              display: true,
              text: 'Tokens',
              color: '#8b949e',
            },
            grid: {
              color: '#21262d',
            },
            ticks: {
              color: '#8b949e',
              callback: (value) => Number(value).toLocaleString(),
            },
          },
          y1: {
            type: 'linear',
            position: 'right',
            min: 0,
            max: 100,
            title: {
              display: true,
              text: 'Cache Hit %',
              color: '#8b949e',
            },
            grid: {
              drawOnChartArea: false,
            },
            ticks: {
              color: '#8b949e',
              callback: (value) => `${value}%`,
            },
          },
        },
      },
    });
  }

  /**
   * Update chart with new turn list.
   * @param {Array<Object>} turns
   */
  updateData(turns) {
    this.turns = turns || [];
    if (!this.chart) {
      if (typeof window.Chart !== 'undefined') {
        this._initChart();
      }
      if (!this.chart) return;
    }

    const labels = [];
    const systemData = [];
    const toolsData = [];
    const historyData = [];
    const resultsData = [];
    const thoughtsData = [];
    const outputData = [];
    const cacheHitData = [];

    this.turns.forEach((t, i) => {
      const idx = t.turn_index !== undefined ? t.turn_index : i;
      labels.push(`Turn #${idx}`);

      // Calculate token segments
      let sys = 0;
      let tls = 0;
      let hist = 0;
      let res = 0;
      let tht = 0;

      if (t.tokens) {
        sys = t.tokens.system || 0;
        tls = t.tokens.tools || 0;
        hist = t.tokens.history || 0;
        res = t.tokens.toolResults || 0;
        tht = t.tokens.thoughts || 0;
      } else {
        if (t.system_blocks) sys = t.system_blocks.reduce((acc, b) => acc + (b.token_count || 0), 0);
        if (t.tool_defs) tls = t.tool_defs.reduce((acc, b) => acc + (b.token_count || 0), 0);
        if (t.conversation_history) hist = t.conversation_history.reduce((acc, b) => acc + (b.token_count || 0), 0);
        if (t.tool_results) res = t.tool_results.reduce((acc, b) => acc + (b.token_count || 0), 0);
        if (t.assistant_blocks) {
          tht = t.assistant_blocks
            .filter((b) => b.metadata && b.metadata.type === 'thinking')
            .reduce((acc, b) => acc + (b.token_count || 0), 0);
        }
      }

      const out = t.output_tokens || 0;
      const inp = t.input_tokens || (sys + tls + hist + res);
      const cached = t.cached_read_tokens || (t.cache && t.cache.readTokens) || 0;
      const hitRatio = inp > 0 ? Math.round((cached / inp) * 1000) / 10 : 0;

      systemData.push(sys);
      toolsData.push(tls);
      historyData.push(hist);
      resultsData.push(res);
      thoughtsData.push(tht);
      outputData.push(out);
      cacheHitData.push(hitRatio);
    });

    this.chart.data.labels = labels;
    this.chart.data.datasets[0].data = systemData;
    this.chart.data.datasets[1].data = toolsData;
    this.chart.data.datasets[2].data = historyData;
    this.chart.data.datasets[3].data = resultsData;
    this.chart.data.datasets[4].data = thoughtsData;
    this.chart.data.datasets[5].data = outputData;
    this.chart.data.datasets[6].data = cacheHitData;

    this.chart.update();
  }
}

window.DashboardCharts = DashboardCharts;
