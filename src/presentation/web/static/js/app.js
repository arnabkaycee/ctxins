/**
 * Main application coordinator for ctxins Web Dashboard.
 */
class DashboardApp {
  constructor() {
    this.activeSessionId = null;
    this.sessions = [];
    this.turns = [];
    this.violations = [];
    this.summary = null;
    this.selectedTurnIndex = 0;

    this.wsClient = null;
    this.charts = null;

    // DOM Elements
    this.sessionSelect = document.getElementById('session-select');
    this.statusPill = document.getElementById('connection-status');
    this.statusText = document.getElementById('status-text');
    this.exportDropdown = document.getElementById('export-dropdown');
    this.exportDropdownBtn = document.getElementById('export-dropdown-btn');
    this.exportDropdownMenu = document.getElementById('export-dropdown-menu');
    this.exportBtn = document.getElementById('export-btn');
    this.exportMdBtn = document.getElementById('export-md-btn');
    this.navDemoBtn = document.getElementById('nav-demo-btn');
    this.blocksExpandAllBtn = document.getElementById('blocks-expand-all-btn');
    this.blocksCollapseAllBtn = document.getElementById('blocks-collapse-all-btn');
    this.collapsedExchanges = new Set();
    this.collapsedSections = new Set();

    // KPI Elements
    this.kpiTokens = document.getElementById('kpi-tokens');
    this.kpiCacheHit = document.getElementById('kpi-cache-hit');
    this.kpiSpend = document.getElementById('kpi-spend');
    this.kpiAvoidable = document.getElementById('kpi-avoidable');
    this.kpiPollutionScore = document.getElementById('kpi-pollution-score');
    this.pollutionMeterFill = document.getElementById('pollution-meter-fill');
    this.pollutionLevelText = document.getElementById('pollution-level-text');

    // Context Window Capacity Elements
    this.capacitySection = document.getElementById('context-capacity-section');
    this.capacityModelBadge = document.getElementById('capacity-model-badge');
    this.capacityTurnTag = document.getElementById('capacity-turn-tag');
    this.capacityUsageBadge = document.getElementById('capacity-usage-badge');
    this.capacityProgressBar = document.getElementById('capacity-progress-bar');
    this.capacityUsedK = document.getElementById('capacity-used-k');
    this.capacityUsedExact = document.getElementById('capacity-used-exact');
    this.capacityAvailableK = document.getElementById('capacity-available-k');
    this.capacityAvailableExact = document.getElementById('capacity-available-exact');
    this.capacityRemainingK = document.getElementById('capacity-remaining-k');
    this.capacityRemainingPct = document.getElementById('capacity-remaining-pct');

    // Feeds & Tables
    this.recommendationsFeed = document.getElementById('recommendations-feed');
    this.recommendationsCount = document.getElementById('recommendations-count');
    this.turnTitle = document.getElementById('selected-turn-title');
    this.turnInspectorSelect = document.getElementById('turn-inspector-select');
    this.turnPrevBtn = document.getElementById('turn-prev-btn');
    this.turnNextBtn = document.getElementById('turn-next-btn');
    this.turnLatestBtn = document.getElementById('turn-latest-btn');
    this._userPinnedHistoricalTurn = false;
    this.turnMetaRibbon = document.getElementById('turn-meta-ribbon');
    this.autoDiffRibbon = document.getElementById('auto-diff-ribbon');
    this.blocksTableBody = document.getElementById('blocks-table-body');
    this.contextProportionBar = document.getElementById('context-proportion-bar');
    this.filterChipsContainer = document.getElementById('blocks-filter-chips');
    this.currentBlockFilter = 'ALL';

    // Diff Elements
    this.diffT1 = document.getElementById('diff-t1');
    this.diffT2 = document.getElementById('diff-t2');
    this.diffBtn = document.getElementById('diff-btn');
    this.diffResults = document.getElementById('diff-results');

    // Modal & JSON Viewer Elements
    this.modalOverlay = document.getElementById('block-modal');
    this.modalTitle = document.getElementById('modal-title');
    this.modalTypeBadge = document.getElementById('modal-type-badge');
    this.modalCloseBtn = document.getElementById('modal-close-btn');
    this.modalBody = document.getElementById('modal-body');
    this.modalTreeContainer = document.getElementById('modal-tree-container');
    this.modalRawContainer = document.getElementById('modal-raw-container');
    this.modalExpandAllBtn = document.getElementById('modal-expand-all-btn');
    this.modalCollapseAllBtn = document.getElementById('modal-collapse-all-btn');
    this.modalViewTreeBtn = document.getElementById('modal-view-tree-btn');
    this.modalViewRawBtn = document.getElementById('modal-view-raw-btn');
    this.modalCopyBtn = document.getElementById('modal-copy-btn');
    this.modalSearchInput = document.getElementById('modal-search-input');
    this.modalSearchMatches = document.getElementById('modal-search-matches');
    this.modalToolbar = document.getElementById('modal-toolbar');

    this.jsonViewer = null;
  }

  async init() {
    this._bindEvents();

    // Initialize Charts
    this.charts = new DashboardCharts('token-chart', (turnIndex, userAction) => {
      this.selectTurn(turnIndex, userAction !== undefined ? userAction : true);
    });

    // Initialize JSON Viewer
    if (typeof window.JsonViewer !== 'undefined') {
      this.jsonViewer = new JsonViewer({
        treeContainer: this.modalTreeContainer,
        rawContainer: this.modalRawContainer,
        toolbar: this.modalToolbar,
        expandAllBtn: this.modalExpandAllBtn,
        collapseAllBtn: this.modalCollapseAllBtn,
        viewTreeBtn: this.modalViewTreeBtn,
        viewRawBtn: this.modalViewRawBtn,
        copyBtn: this.modalCopyBtn,
        searchInput: this.modalSearchInput,
        searchMatches: this.modalSearchMatches,
        typeBadge: this.modalTypeBadge,
      });
    }

    // Initialize WebSocket client
    this.wsClient = new WSClient({
      onEvent: (event) => this.handleEvent(event),
      onStatusChange: (status) => this.updateConnectionStatus(status),
    });

    // Initial fetch of active sessions
    await this.refreshSessions();

    // Start live WebSocket stream
    this.wsClient.connect(this.activeSessionId);
  }

  _bindEvents() {
    if (this.sessionSelect) {
      this.sessionSelect.addEventListener('change', (e) => {
        const sid = e.target.value;
        if (sid) {
          this.switchSession(sid);
        }
      });
    }

    if (this.exportDropdownBtn) {
      this.exportDropdownBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.toggleExportDropdown();
      });
    }

    if (this.exportBtn) {
      this.exportBtn.addEventListener('click', () => {
        this.closeExportDropdown();
        this.exportSession();
      });
    }

    if (this.exportMdBtn) {
      this.exportMdBtn.addEventListener('click', () => {
        this.closeExportDropdown();
        this.exportMarkdownReport();
      });
    }

    document.addEventListener('click', (e) => {
      if (
        this.exportDropdown &&
        (this.exportDropdown.classList.contains('open') ||
          (this.exportDropdownMenu && this.exportDropdownMenu.classList.contains('active')))
      ) {
        if (!e.target.closest('#export-dropdown')) {
          this.closeExportDropdown();
        }
      }
    });

    if (this.navDemoBtn) {
      this.navDemoBtn.addEventListener('click', (e) => {
        e.preventDefault();
        this.loadDemoSession();
      });
    }

    if (this.diffBtn) {
      this.diffBtn.addEventListener('click', () => this.computeDiff());
    }

    if (this.diffT1) {
      this.diffT1.addEventListener('change', () => this.computeDiff());
    }

    if (this.diffT2) {
      this.diffT2.addEventListener('change', () => this.computeDiff());
    }

    const diffDetails = document.querySelector('.diff-advanced-details');
    if (diffDetails) {
      diffDetails.addEventListener('toggle', () => {
        if (diffDetails.open && (!this.diffResults.innerHTML || this.diffResults.innerHTML.trim() === '')) {
          this.computeDiff();
        }
      });
    }

    if (this.diffResults) {
      this.diffResults.addEventListener('click', (e) => {
        const inspectBtn = e.target.closest('.diff-block-inspect-btn');
        if (inspectBtn) {
          e.stopPropagation();
          const blockId = inspectBtn.dataset.blockId;
          const turnVal = inspectBtn.dataset.turn ? parseInt(inspectBtn.dataset.turn, 10) : null;
          this.locateAndHighlightBlock(blockId, turnVal, true);
          return;
        }

        const pill = e.target.closest('.diff-block-pill');
        if (pill) {
          const blockId = pill.dataset.blockId;
          const turnVal = pill.dataset.turn ? parseInt(pill.dataset.turn, 10) : null;
          this.locateAndHighlightBlock(blockId, turnVal, false);
        }
      });

      this.diffResults.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          const inspectBtn = e.target.closest('.diff-block-inspect-btn');
          if (inspectBtn) {
            e.preventDefault();
            e.stopPropagation();
            const blockId = inspectBtn.dataset.blockId;
            const turnVal = inspectBtn.dataset.turn ? parseInt(inspectBtn.dataset.turn, 10) : null;
            this.locateAndHighlightBlock(blockId, turnVal, true);
            return;
          }

          const pill = e.target.closest('.diff-block-pill');
          if (pill) {
            e.preventDefault();
            const blockId = pill.dataset.blockId;
            const turnVal = pill.dataset.turn ? parseInt(pill.dataset.turn, 10) : null;
            this.locateAndHighlightBlock(blockId, turnVal, false);
          }
        }
      });
    }

    if (this.filterChipsContainer) {
      this.filterChipsContainer.addEventListener('click', (e) => {
        const chip = e.target.closest('.filter-chip');
        if (!chip) return;
        const filter = chip.dataset.filter || chip.getAttribute('data-filter');
        if (filter) {
          this.setBlockFilter(filter);
        }
      });
    }

    if (this.blocksExpandAllBtn) {
      this.blocksExpandAllBtn.addEventListener('click', () => {
        this.expandAllSections();
      });
    }

    if (this.blocksCollapseAllBtn) {
      this.blocksCollapseAllBtn.addEventListener('click', () => {
        this.collapseAllSections();
      });
    }

    if (this.modalCloseBtn) {
      this.modalCloseBtn.addEventListener('click', () => this.closeModal());
    }

    if (this.modalOverlay) {
      this.modalOverlay.addEventListener('click', (e) => {
        if (e.target === this.modalOverlay) {
          this.closeModal();
        }
      });
    }

    if (this.turnInspectorSelect) {
      this.turnInspectorSelect.addEventListener('change', (e) => {
        const val = parseInt(e.target.value, 10);
        if (!isNaN(val)) {
          this.selectTurn(val, true);
        }
      });
    }

    if (this.turnPrevBtn) {
      this.turnPrevBtn.addEventListener('click', () => this.navigateTurn(-1));
    }

    if (this.turnNextBtn) {
      this.turnNextBtn.addEventListener('click', () => this.navigateTurn(1));
    }

    if (this.turnLatestBtn) {
      this.turnLatestBtn.addEventListener('click', () => this.navigateLatestTurn());
    }

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        if (
          this.exportDropdown &&
          (this.exportDropdown.classList.contains('open') ||
            (this.exportDropdownMenu && this.exportDropdownMenu.classList.contains('active')))
        ) {
          this.closeExportDropdown();
        }
        if (this.modalOverlay && this.modalOverlay.classList.contains('active')) {
          this.closeModal();
        }
      } else if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        const tag = document.activeElement ? document.activeElement.tagName : '';
        if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
        if (this.modalOverlay && this.modalOverlay.classList.contains('active')) return;
        if (e.key === 'ArrowLeft') {
          this.navigateTurn(-1);
        } else {
          this.navigateTurn(1);
        }
      }
    });
  }

  toggleExportDropdown() {
    if (this.exportDropdown && this.exportDropdown.classList.contains('open')) {
      this.closeExportDropdown();
    } else {
      this.openExportDropdown();
    }
  }

  openExportDropdown() {
    if (this.exportDropdown) this.exportDropdown.classList.add('open');
    if (this.exportDropdownMenu) this.exportDropdownMenu.classList.add('active');
    if (this.exportDropdownBtn) this.exportDropdownBtn.setAttribute('aria-expanded', 'true');
  }

  closeExportDropdown() {
    if (this.exportDropdown) this.exportDropdown.classList.remove('open');
    if (this.exportDropdownMenu) this.exportDropdownMenu.classList.remove('active');
    if (this.exportDropdownBtn) this.exportDropdownBtn.setAttribute('aria-expanded', 'false');
  }

  expandAllSections() {
    this.collapsedSections.clear();
    this.collapsedExchanges.clear();
    if (!this.blocksTableBody) return;
    const headers = this.blocksTableBody.querySelectorAll('.exchange-group-header, .section-group-header');
    headers.forEach((h) => h.classList.remove('collapsed'));
    const rows = this.blocksTableBody.querySelectorAll('.exchange-item-row');
    rows.forEach((r) => r.classList.remove('exchange-hidden'));
  }

  collapseAllSections() {
    if (!this.blocksTableBody) return;
    const headers = this.blocksTableBody.querySelectorAll('.exchange-group-header, .section-group-header');
    headers.forEach((h) => {
      h.classList.add('collapsed');
      const exKey = h.dataset.exchange;
      const secKey = h.dataset.section;
      if (exKey) this.collapsedExchanges.add(exKey);
      if (secKey) this.collapsedSections.add(secKey);
    });
    const rows = this.blocksTableBody.querySelectorAll('.exchange-item-row');
    rows.forEach((r) => r.classList.add('exchange-hidden'));
  }

  async refreshSessions() {
    try {
      const res = await fetch('/api/v1/sessions');
      if (res.ok) {
        this.sessions = await res.json();
        this._populateSessionSelect();
      }
    } catch (err) {
      console.warn('[DashboardApp] Could not fetch sessions list:', err);
    }
  }

  _populateSessionSelect() {
    if (!this.sessionSelect) return;
    const currentVal = this.sessionSelect.value;
    this.sessionSelect.innerHTML = '';

    if (!this.sessions || this.sessions.length === 0) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'Waiting for agent traffic on proxy...';
      this.sessionSelect.appendChild(opt);
      return;
    }

    this.sessions.forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s.sessionId;
      const harness = s.agentHarness || s.harness || '';
      const harnessTag = harness && harness !== 'unknown' ? ` [${harness}]` : '';
      const model = s.model && s.model !== 'unknown' && s.model !== 'auto-detect' ? ` (${s.model})` : '';
      opt.textContent = `${s.sessionId}${harnessTag}${model}`;
      this.sessionSelect.appendChild(opt);
    });

    if (currentVal && this.sessions.some((s) => s.sessionId === currentVal)) {
      this.sessionSelect.value = currentVal;
      this.activeSessionId = currentVal;
    } else {
      this.activeSessionId = this.sessions[0].sessionId;
      this.sessionSelect.value = this.activeSessionId;
    }
  }

  switchSession(sessionId) {
    if (this.activeSessionId === sessionId) return;
    this.activeSessionId = sessionId;
    this._userPinnedHistoricalTurn = false;
    this.selectedTurnIndex = null;
    if (this.sessionSelect) {
      this.sessionSelect.value = sessionId;
    }
    if (this.wsClient) {
      this.wsClient.switchSession(sessionId);
    }
    this.loadSessionREST(sessionId);
  }

  async loadSessionREST(sessionId) {
    try {
      const res = await fetch(`/api/v1/sessions/${encodeURIComponent(sessionId)}`);
      if (res.ok) {
        const data = await res.json();
        this.summary = data.summary;
        this.turns = data.turns || [];
        this.violations = data.violations || [];
        this.renderAll();
      }
    } catch (err) {
      console.error('[DashboardApp] Failed to load session via REST:', err);
    }
  }

  handleEvent(event) {
    const type = event.type || (event.payload && event.payload.type);
    const sid = event.sessionId || (event.payload && event.payload.sessionId);

    // If active session was not yet set, adopt incoming session
    if (!this.activeSessionId && sid) {
      this.activeSessionId = sid;
      this.refreshSessions();
    }

    // Ignore events for other sessions if activeSessionId is set, except session_created and session_erased
    if (this.activeSessionId && sid && this.activeSessionId !== sid) {
      if (
        type === 'session_created' ||
        type === 'SESSION_CREATED' ||
        type === 'session_erased' ||
        type === 'SESSION_ERASED'
      ) {
        this.refreshSessions();
      }
      return;
    }

    if (type === 'SNAPSHOT' || type === 'snapshot') {
      const payload = event.payload || event;
      if (event.sessionId) {
        this.activeSessionId = event.sessionId;
      }
      this.summary = payload.summary || null;
      this.turns = payload.turns || [];
      this.violations = payload.violations || [];
      this.renderAll();
      this.refreshSessions();
    } else if (type === 'turn_started' || type === 'TURN_STARTED') {
      const tNum = event.payload?.turn_index ?? event.payload?.turnIndex;
      if (this.statusText) {
        this.statusText.textContent = tNum !== undefined ? `Turn #${tNum} in progress...` : 'Turn in progress...';
      }
    } else if (type === 'turn_streaming' || type === 'TURN_STREAMING') {
      const tNum = event.payload?.turn_index ?? event.payload?.turnIndex;
      if (this.statusText) {
        this.statusText.textContent = tNum !== undefined ? `Turn #${tNum} streaming...` : 'Streaming tokens...';
      }
    } else if (type === 'turn_completed' || type === 'TURN_COMPLETED') {
      const payload = event.payload || {};
      const turnData = payload.turn || payload;
      const tIdx = turnData.turn_index !== undefined ? turnData.turn_index : turnData.turnIndex;
      if (tIdx !== undefined) {
        // Normalize fields for uniform internal access
        if (turnData.turn_index === undefined) turnData.turn_index = tIdx;
        if (turnData.input_tokens === undefined && turnData.inputTokens !== undefined) turnData.input_tokens = turnData.inputTokens;
        if (turnData.output_tokens === undefined && turnData.outputTokens !== undefined) turnData.output_tokens = turnData.outputTokens;
        if (turnData.cached_read_tokens === undefined && turnData.cachedReadTokens !== undefined) turnData.cached_read_tokens = turnData.cachedReadTokens;
        if (!turnData.all_blocks && turnData.blocks) turnData.all_blocks = turnData.blocks;

        const existingIdx = this.turns.findIndex(
          (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === tIdx
        );
        if (existingIdx >= 0) {
          this.turns[existingIdx] = turnData;
        } else {
          this.turns.push(turnData);
        }
      }
      if (turnData.violations) {
        turnData.violations.forEach((v) => {
          if (v.turn_index === undefined && v.turnIndex === undefined && tIdx !== undefined) {
            v.turn_index = tIdx;
          }
          this.violations.push(v);
        });
      }
      if (payload.summary) {
        this.summary = payload.summary;
      }
      this.renderAll();
      this.refreshSessions();
      this.updateConnectionStatus('connected');
    } else if (type === 'violation_detected' || type === 'VIOLATION_DETECTED') {
      const violation = event.payload ? event.payload.violation || event.payload : null;
      if (violation) {
        this.violations.push(violation);
        this.renderRecommendations();
      }
    } else if (type === 'session_summary_updated' || type === 'SESSION_SUMMARY_UPDATED') {
      if (event.payload && event.payload.summary) {
        this.summary = event.payload.summary;
        this.renderKPIs();
      }
    } else if (type === 'session_created' || type === 'SESSION_CREATED') {
      this.refreshSessions();
    } else if (type === 'session_erased' || type === 'SESSION_ERASED') {
      if (sid === this.activeSessionId) {
        this.turns = [];
        this.violations = [];
        this.summary = null;
        this._userPinnedHistoricalTurn = false;
        this.selectedTurnIndex = null;
        if (this.statusText) {
          this.statusText.textContent = 'Erased (Unexported)';
        }
        this.renderAll();
      }
      this.refreshSessions();
    } else if (type === 'session_disconnected' || type === 'SESSION_DISCONNECTED') {
      if (sid === this.activeSessionId) {
        if (this.statusText) {
          this.statusText.textContent = 'Disconnected (Preserved)';
        }
      }
      this.refreshSessions();
    }
  }

  updateConnectionStatus(status) {
    if (!this.statusPill || !this.statusText) return;
    this.statusPill.className = `status-pill ${status}`;
    if (status === 'connected') {
      if (!this.turns || this.turns.length === 0) {
        this.statusText.textContent = 'Listening on 127.0.0.1:8080 - Waiting for agent traffic';
      } else {
        this.statusText.textContent = 'Live Connected';
      }
    } else if (status === 'reconnecting') {
      this.statusText.textContent = 'Reconnecting...';
    } else {
      this.statusText.textContent = 'Listening on 127.0.0.1:8080 - Waiting for agent traffic';
    }
  }

  renderAll() {
    this.renderKPIs();
    this.renderContextCapacity();
    if (this.charts) {
      this.charts.updateData(this.turns);
    }
    this.renderRecommendations();
    this._populateDiffSelects();
    this._populateTurnSelect();

    // Select latest turn if following live, or preserve user-pinned historical turn if valid
    if (this.turns.length > 0) {
      const lastTurn = this.turns[this.turns.length - 1];
      const latestIdx =
        lastTurn.turn_index !== undefined
          ? lastTurn.turn_index
          : lastTurn.turnIndex !== undefined
          ? lastTurn.turnIndex
          : this.turns.length - 1;

      if (!this._userPinnedHistoricalTurn) {
        this.selectTurn(latestIdx);
      } else {
        const valid = this.turns.some(
          (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === this.selectedTurnIndex
        );
        this.selectTurn(valid ? this.selectedTurnIndex : latestIdx);
      }
    } else {
      this.renderEmptyTurnInspector();
    }
  }

  renderKPIs() {
    const s = this.summary || {};

    const totalInput = s.totalInputTokens ?? s.total_input_tokens ?? 0;
    const totalOutput = s.totalOutputTokens ?? s.total_output_tokens ?? 0;
    const totalTokens = totalInput + totalOutput;
    if (this.kpiTokens) this.kpiTokens.textContent = totalTokens.toLocaleString();

    const hitRatio = s.cacheHitRatio ?? s.cache_hit_ratio ?? 0;
    const hitPct = Math.round(hitRatio * 1000) / 10;
    if (this.kpiCacheHit) this.kpiCacheHit.textContent = `${hitPct}%`;

    const spend = Number(s.estimatedCostUSD ?? s.estimated_cost_usd ?? 0).toFixed(4);
    if (this.kpiSpend) this.kpiSpend.textContent = `$${spend}`;

    const avoidable = Number(s.potentialSavingsUSD ?? s.potential_savings_usd ?? 0).toFixed(4);
    if (this.kpiAvoidable) this.kpiAvoidable.textContent = `$${avoidable}`;

    const score = Number(s.pollutionScore ?? s.pollution_score ?? 0);
    if (this.kpiPollutionScore) this.kpiPollutionScore.textContent = `${score.toFixed(1)} / 100`;

    if (this.pollutionMeterFill) {
      this.pollutionMeterFill.style.width = `${Math.min(100, Math.max(0, score))}%`;
      if (score < 20) {
        this.pollutionMeterFill.style.backgroundColor = 'var(--color-success)';
        if (this.pollutionLevelText) this.pollutionLevelText.textContent = 'Pristine Clean';
      } else if (score < 50) {
        this.pollutionMeterFill.style.backgroundColor = 'var(--color-warning)';
        if (this.pollutionLevelText) this.pollutionLevelText.textContent = 'Moderate Bloat';
      } else {
        this.pollutionMeterFill.style.backgroundColor = 'var(--color-critical)';
        if (this.pollutionLevelText) this.pollutionLevelText.textContent = 'Critical Pollution';
      }
    }
  }

  getModelCapacity(modelName, provider = null) {
    if (!modelName) return 200000;
    const m = String(modelName).toLowerCase();
    if (m.includes('gemini-1.5-pro') || m.includes('gemini-2.5-pro')) return 2000000;
    if (m.includes('gemini-1.5-flash') || m.includes('gemini-2.0-flash')) return 1000000;
    if (m.includes('gpt-4o-mini') || m.includes('o1-mini') || m.includes('gpt-4o')) return 128000;
    if (m.includes('claude') || m.includes('sonnet') || m.includes('haiku') || m.includes('opus')) return 200000;
    if (provider) {
      const p = String(provider).toLowerCase();
      if (p.includes('gemini') || p.includes('google')) return 2000000;
      if (p.includes('openai')) return 128000;
      if (p.includes('anthropic')) return 200000;
    }
    return 200000;
  }

  renderContextCapacity() {
    if (!this.capacitySection) return;

    const s = this.summary || {};
    const selectedTurn = this.getSelectedTurn();
    const lastTurn = this.turns.length > 0 ? this.turns[this.turns.length - 1] : null;
    const activeTurn = selectedTurn || lastTurn;

    const currentSession = this.sessions.find((sess) => sess.sessionId === this.activeSessionId);
    const model =
      activeTurn?.model ||
      currentSession?.model ||
      s.model ||
      'claude-3-5-sonnet';
    const provider =
      activeTurn?.provider ||
      currentSession?.provider ||
      s.provider ||
      'anthropic';

    const capacityTokens =
      s.contextCapacityTokens ||
      this.getModelCapacity(model, provider);

    // Used tokens is active turn's input tokens (or total tokens in turn context)
    const usedTokens = activeTurn
      ? (activeTurn.input_tokens ?? activeTurn.inputTokens ?? activeTurn.total_tokens ?? 0)
      : (s.latestContextTokens ?? 0);

    const usageRatio = capacityTokens > 0 ? usedTokens / capacityTokens : 0;
    const usagePct = Math.min(100, Math.max(0, usageRatio * 100));

    const usedK = (usedTokens / 1000).toFixed(1) + 'K';
    const capacityK = (capacityTokens / 1000).toFixed(1) + 'K';
    const remainingTokens = Math.max(0, capacityTokens - usedTokens);
    const remainingK = (remainingTokens / 1000).toFixed(1) + 'K';
    const remainingPct = Math.max(0, 100 - usagePct).toFixed(1);

    if (this.capacityModelBadge) {
      this.capacityModelBadge.textContent = model;
    }

    if (this.capacityTurnTag) {
      if (selectedTurn) {
        const tIdx = selectedTurn.turn_index ?? selectedTurn.turnIndex ?? 0;
        this.capacityTurnTag.textContent = `Turn #${Number(tIdx) + 1} Context`;
      } else {
        this.capacityTurnTag.textContent = 'Active Context Window';
      }
    }

    if (this.capacityUsageBadge) {
      this.capacityUsageBadge.textContent = `${usagePct.toFixed(1)}% Usage`;
      this.capacityUsageBadge.classList.remove('usage-warn', 'usage-critical');
      if (usagePct >= 80) {
        this.capacityUsageBadge.classList.add('usage-critical');
      } else if (usagePct >= 50) {
        this.capacityUsageBadge.classList.add('usage-warn');
      }
    }

    if (this.capacityProgressBar) {
      this.capacityProgressBar.style.width = `${usagePct}%`;
      this.capacityProgressBar.classList.remove('usage-warn', 'usage-critical');
      if (usagePct >= 80) {
        this.capacityProgressBar.classList.add('usage-critical');
      } else if (usagePct >= 50) {
        this.capacityProgressBar.classList.add('usage-warn');
      }
    }

    if (this.capacityUsedK) {
      this.capacityUsedK.textContent = usedK;
    }
    if (this.capacityUsedExact) {
      this.capacityUsedExact.textContent = `(${usedTokens.toLocaleString()} tokens)`;
    }

    if (this.capacityAvailableK) {
      this.capacityAvailableK.textContent = capacityK;
    }
    if (this.capacityAvailableExact) {
      this.capacityAvailableExact.textContent = `(${capacityTokens.toLocaleString()} max)`;
    }

    if (this.capacityRemainingK) {
      this.capacityRemainingK.textContent = remainingK;
    }
    if (this.capacityRemainingPct) {
      this.capacityRemainingPct.textContent = `(${remainingPct}% free)`;
    }
  }

  renderRecommendations() {
    if (!this.recommendationsFeed) return;
    this.recommendationsFeed.innerHTML = '';

    if (this.recommendationsCount) {
      this.recommendationsCount.textContent = `${this.violations.length} active`;
    }

    if (!this.violations || this.violations.length === 0) {
      this.recommendationsFeed.innerHTML = `
        <div class="empty-state">
          <div class="empty-state-icon">✨</div>
          <div style="font-weight: 600; color: var(--text-heading);">Zero Context Violations</div>
          <div style="font-size: 12px; margin-top: 4px;">Context cache boundaries and prompt sizing are optimal.</div>
        </div>
      `;
      return;
    }

    // Determine current turn index
    const currentTurn =
      this.selectedTurnIndex !== null && this.selectedTurnIndex !== undefined
        ? this.selectedTurnIndex
        : (this.turns.length > 0
            ? (this.turns[this.turns.length - 1].turn_index ?? this.turns[this.turns.length - 1].turnIndex ?? 0)
            : 0);

    // Group violations by ruleId or suggestedFix
    const groupsMap = new Map();
    this.violations.forEach((v) => {
      const ruleId = v.rule_id || v.ruleId || '';
      const fix = v.suggested_fix || v.suggestedFix || '';
      const title = v.title || ruleId || 'Alert';
      const groupKey = ruleId || fix || title;
      if (!groupsMap.has(groupKey)) {
        groupsMap.set(groupKey, []);
      }
      groupsMap.get(groupKey).push(v);
    });

    const priorityOrder = { CRITICAL: 0, WARN: 1, INFO: 2 };
    const sortedGroups = Array.from(groupsMap.values()).map((viols) => {
      const sortedViols = [...viols].sort((a, b) => {
        const tA = a.turn_index ?? a.turnIndex ?? 0;
        const tB = b.turn_index ?? b.turnIndex ?? 0;
        return tA - tB;
      });
      const first = sortedViols[0];
      const ruleId = first.rule_id || first.ruleId || 'RULE';
      const title = first.title || ruleId;
      const fix =
        sortedViols.find((x) => x.suggested_fix || x.suggestedFix)?.suggested_fix ||
        sortedViols.find((x) => x.suggested_fix || x.suggestedFix)?.suggestedFix ||
        '';

      let maxSev = 'INFO';
      for (const x of sortedViols) {
        const s = (x.severity || 'INFO').toUpperCase();
        if (s === 'CRITICAL') {
          maxSev = 'CRITICAL';
          break;
        } else if (s === 'WARN') {
          maxSev = 'WARN';
        }
      }

      const earlierViolations = sortedViols.filter(
        (x) => (x.turn_index ?? x.turnIndex ?? 0) < currentTurn
      );
      const currentViolations = sortedViols.filter(
        (x) => (x.turn_index ?? x.turnIndex ?? 0) === currentTurn
      );
      const currentViolation = currentViolations.length > 0 ? currentViolations[0] : null;

      const totalWaste = sortedViols.reduce(
        (sum, x) => sum + Number(x.estimated_waste_usd ?? x.estimatedWasteUSD ?? 0),
        0
      );
      const earlierWaste = earlierViolations.reduce(
        (sum, x) => sum + Number(x.estimated_waste_usd ?? x.estimatedWasteUSD ?? 0),
        0
      );
      const currentWaste = currentViolation
        ? Number(currentViolation.estimated_waste_usd ?? currentViolation.estimatedWasteUSD ?? 0)
        : 0;

      const uniqueTurns = Array.from(
        new Set(sortedViols.map((x) => x.turn_index ?? x.turnIndex ?? 0))
      ).sort((a, b) => a - b);
      const earlierTurns = Array.from(
        new Set(earlierViolations.map((x) => x.turn_index ?? x.turnIndex ?? 0))
      ).sort((a, b) => a - b);

      return {
        ruleId,
        title,
        fix,
        severity: maxSev,
        totalOccurrences: sortedViols.length,
        uniqueTurns,
        earlierViolations,
        earlierTurns,
        currentViolation,
        totalWaste,
        earlierWaste,
        currentWaste,
        violations: sortedViols,
        primaryViolation: currentViolation || sortedViols[sortedViols.length - 1],
      };
    });

    sortedGroups.sort((a, b) => {
      const pA = priorityOrder[a.severity] ?? 3;
      const pB = priorityOrder[b.severity] ?? 3;
      if (pA !== pB) return pA - pB;
      return b.totalWaste - a.totalWaste;
    });

    if (this.recommendationsCount) {
      this.recommendationsCount.textContent = `${sortedGroups.length} active (${this.violations.length} total)`;
    }

    sortedGroups.forEach((group) => {
      const v = group.primaryViolation;
      const card = document.createElement('div');
      const sev = group.severity;
      card.className = `violation-card severity-${sev}`;

      const badgeClass =
        sev === 'CRITICAL' ? 'badge-critical' : sev === 'WARN' ? 'badge-warn' : 'badge-info';
      const wasteStr =
        group.totalWaste > 0 ? `$${group.totalWaste.toFixed(4)} total waste` : '';

      const count = group.totalOccurrences;
      const turnCount = group.uniqueTurns.length;
      const occBadge = `<span class="badge badge-occurrence" style="margin-left: 6px; font-size: 11px; background: rgba(110, 118, 129, 0.2); color: var(--text-secondary); border-radius: 12px; padding: 2px 8px;">${count} violation${count !== 1 ? 's' : ''}${turnCount > 1 ? ` across ${turnCount} turns` : ''}</span>`;

      let currentTurnHtml = '';
      if (group.currentViolation) {
        const curWaste =
          group.currentWaste > 0 ? ` ($${group.currentWaste.toFixed(4)} waste)` : '';
        currentTurnHtml = `
          <div style="margin-bottom: 3px;">
            <strong style="color: var(--text-primary);">Current Turn (#${currentTurn}):</strong> 
            <span style="color: var(--color-warn, #d29922); font-weight: 500;">Active${curWaste}</span>
            ${group.currentViolation.message ? ` — <span style="color: var(--text-secondary);">${group.currentViolation.message}</span>` : ''}
          </div>
        `;
      } else {
        currentTurnHtml = `
          <div style="margin-bottom: 3px;">
            <strong style="color: var(--text-primary);">Current Turn (#${currentTurn}):</strong> 
            <span style="color: var(--color-success, #3fb950); font-weight: 500;">Clean / Not triggered</span>
          </div>
        `;
      }

      let earlierTurnsHtml = '';
      if (group.earlierViolations.length > 0) {
        const earlierWasteStr =
          group.earlierWaste > 0 ? ` ($${group.earlierWaste.toFixed(4)} waste)` : '';
        const turnsList = group.earlierTurns.map((t) => `#${t}`).join(', ');
        earlierTurnsHtml = `
          <div style="color: var(--text-secondary); font-size: 11px;">
            <strong style="color: var(--text-primary);">Earlier Turns (${turnsList}):</strong> 
            ${group.earlierViolations.length} violation${group.earlierViolations.length !== 1 ? 's' : ''}${earlierWasteStr}
          </div>
        `;
      } else {
        earlierTurnsHtml = `
          <div style="color: var(--text-muted); font-size: 11px;">
            <strong style="color: var(--text-primary);">Earlier Turns:</strong> None (first occurrence)
          </div>
        `;
      }

      const isRecurring =
        (group.ruleId || '').toUpperCase().includes('CTX004') ||
        (group.ruleId || '').toUpperCase().includes('CTX-004') ||
        (group.title || '').toLowerCase().includes('recurring');

      card.innerHTML = `
        <div class="violation-header">
          <div class="violation-title-group">
            <span class="badge ${badgeClass}">${sev}</span>
            <span class="violation-title">${group.title || group.ruleId || 'Heuristic Alert'}</span>
            ${occBadge}
          </div>
          ${wasteStr ? `<span class="violation-waste">${wasteStr}</span>` : ''}
        </div>
        <div class="violation-breakdown" style="margin: 8px 0 6px 0; font-size: 12px; background: rgba(255, 255, 255, 0.03); border-radius: 6px; padding: 6px 10px; border: 1px solid rgba(255, 255, 255, 0.06);">
          ${currentTurnHtml}
          ${earlierTurnsHtml}
        </div>
        ${group.fix ? `<div class="violation-fix">💡 Fix: ${group.fix}</div>` : ''}
        <div class="violation-actions">
          <button class="violation-action-btn inspect-culprit-btn" type="button">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" style="vertical-align: -2px; margin-right: 4px;">
              <path d="M11.742 10.344a6.5 6.5 0 1 0-1.397 1.398h-.001c.03.04.062.078.098.115l3.85 3.85a1 1 0 0 0 1.415-1.414l-3.85-3.85a1.007 1.007 0 0 0-.115-.1zM12 6.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0z"/>
            </svg>
            Inspect Culprit
          </button>
          <button class="violation-action-btn copy-directive-btn" type="button">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" style="vertical-align: -2px; margin-right: 4px;">
              <path d="M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25Z"/>
              <path d="M5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25Zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25Z"/>
            </svg>
            Copy Directive
          </button>
          ${
            isRecurring
              ? `
          <button class="violation-action-btn shrink-context-btn" type="button" title="Copy context compaction directive to prune repetitive results">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" style="vertical-align: -2px; margin-right: 4px;">
              <path d="M9.5 0a.75.75 0 0 1 .75.75v2.5a.75.75 0 0 1-1.5 0V1.5H6.75a.75.75 0 0 1 0-1.5h2.75zM0 6.5a.75.75 0 0 1 .75-.75h2.5a.75.75 0 0 1 0 1.5H1.5v2h1.75a.75.75 0 0 1 0 1.5H.75A.75.75 0 0 1 0 10V6.5zm14.5 0a.75.75 0 0 1 .75.75V10a.75.75 0 0 1-.75.75h-2.5a.75.75 0 0 1 0-1.5h1.75v-2h-1.75a.75.75 0 0 1 0-1.5h2.75zM6.5 16a.75.75 0 0 1-.75-.75v-2.5a.75.75 0 0 1 1.5 0v1.75h2a.75.75 0 0 1 0 1.5H6.5z"/>
            </svg>
            Shrink Context
          </button>
          <button class="violation-action-btn new-session-btn" type="button" title="Copy new session reset directive with preserved state">
            <svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor" style="vertical-align: -2px; margin-right: 4px;">
              <path d="M8 3a5 5 0 1 0 4.546 2.914.5.5 0 0 1 .908-.417A6 6 0 1 1 8 2v1z"/>
              <path d="M8 4.466V.534a.25.25 0 0 1 .41-.192l2.36 1.966c.12.1.12.284 0 .384L8.41 4.658A.25.25 0 0 1 8 4.466z"/>
            </svg>
            New Session
          </button>
          `
              : ''
          }
        </div>
      `;

      const inspectBtn = card.querySelector('.inspect-culprit-btn');
      if (inspectBtn) {
        inspectBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          const targetBlockId =
            v.block_id ||
            v.blockId ||
            (Array.isArray(v.block_ids) && v.block_ids.length > 0 ? v.block_ids[0] : null) ||
            (Array.isArray(v.blockIds) && v.blockIds.length > 0 ? v.blockIds[0] : null);

          let targetTurn =
            v.turn_index !== undefined
              ? v.turn_index
              : v.turnIndex !== undefined
              ? v.turnIndex
              : null;

          if ((targetTurn === null || targetTurn === undefined) && targetBlockId && this.turns) {
            for (const t of this.turns) {
              const blocks =
                t.all_blocks ||
                t.blocks || [
                  ...(t.system_blocks || t.systemBlocks || []),
                  ...(t.tool_defs || t.toolDefs || []),
                  ...(t.conversation_history || t.conversationHistory || []),
                  ...(t.tool_results || t.toolResults || []),
                  ...(t.assistant_blocks || t.assistantBlocks || []),
                ];
              if (blocks.some((b) => (b.block_id || b.blockId) === targetBlockId)) {
                targetTurn = t.turn_index !== undefined ? t.turn_index : t.turnIndex;
                break;
              }
            }
          }

          if (targetBlockId) {
            this.locateAndHighlightBlock(targetBlockId, targetTurn);
          } else {
            if (targetTurn !== null && targetTurn !== undefined) {
              this.selectTurn(targetTurn, true);
            }
            document.getElementById('blocks-table-body')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
            const turnLabel = targetTurn !== null && targetTurn !== undefined ? `Turn #${Number(targetTurn) + 1}` : 'Current turn';
            this.showToast(`⚠️ Turn-level alert: Violation applies across ${turnLabel}`);
            const subtitle = document.querySelector('.inspector-panel .panel-subtitle');
            if (subtitle) {
              const originalText = subtitle.textContent;
              subtitle.textContent = `⚠️ Turn-level alert: ${v.title || v.rule_id || 'Alert'} applies across ${turnLabel}`;
              subtitle.style.color = 'var(--color-warning)';
              setTimeout(() => {
                subtitle.textContent = originalText;
                subtitle.style.color = '';
              }, 2500);
            }
          }
        });
      }

      const copyBtn = card.querySelector('.copy-directive-btn');
      if (copyBtn) {
        copyBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const directive = this.generateDirective(v);
          const originalHTML = copyBtn.innerHTML;

          try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              await navigator.clipboard.writeText(directive);
            } else {
              this.fallbackCopyText(directive);
            }
          } catch (err) {
            console.warn('Clipboard write failed, using fallback:', err);
            this.fallbackCopyText(directive);
          }

          copyBtn.textContent = '✓ Copied!';
          copyBtn.classList.add('copied');
          setTimeout(() => {
            copyBtn.innerHTML = originalHTML;
            copyBtn.classList.remove('copied');
          }, 2000);
        });
      }

      const shrinkBtn = card.querySelector('.shrink-context-btn');
      if (shrinkBtn) {
        shrinkBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const compactDirective =
            '/compact Recurring execution results exceed context threshold. Summarize earlier command outputs into concise findings and purge raw stdout payloads from prompt context.';
          const originalHTML = shrinkBtn.innerHTML;
          try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              await navigator.clipboard.writeText(compactDirective);
            } else {
              this.fallbackCopyText(compactDirective);
            }
          } catch (_) {
            this.fallbackCopyText(compactDirective);
          }
          shrinkBtn.textContent = '✓ Copied /compact';
          shrinkBtn.classList.add('copied');
          this.showToast('Copied context compaction directive (/compact) to clipboard');
          setTimeout(() => {
            shrinkBtn.innerHTML = originalHTML;
            shrinkBtn.classList.remove('copied');
          }, 2000);
        });
      }

      const newSessionBtn = card.querySelector('.new-session-btn');
      if (newSessionBtn) {
        newSessionBtn.addEventListener('click', async (e) => {
          e.stopPropagation();
          const clearDirective =
            '/clear Reset session context to eliminate recurring results. Active carryover: Preserving current task goal, key modified files, and latest test status.';
          const originalHTML = newSessionBtn.innerHTML;
          try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
              await navigator.clipboard.writeText(clearDirective);
            } else {
              this.fallbackCopyText(clearDirective);
            }
          } catch (_) {
            this.fallbackCopyText(clearDirective);
          }
          newSessionBtn.textContent = '✓ Copied /clear';
          newSessionBtn.classList.add('copied');
          this.showToast('Copied new session reset directive (/clear) to clipboard');
          setTimeout(() => {
            newSessionBtn.innerHTML = originalHTML;
            newSessionBtn.classList.remove('copied');
          }, 2000);
        });
      }

      this.recommendationsFeed.appendChild(card);
    });
  }

  setBlockFilter(filter) {
    this.currentBlockFilter = filter;
    if (this.filterChipsContainer) {
      const chips = this.filterChipsContainer.querySelectorAll('.filter-chip');
      chips.forEach((chip) => {
        chip.classList.toggle('active', (chip.dataset.filter || chip.getAttribute('data-filter')) === filter);
      });
    }
    const currentTurn = this.getSelectedTurn();
    if (currentTurn) {
      this.renderBlocksTable(currentTurn);
    }
  }

  getSelectedTurn() {
    return this.turns.find(
      (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === this.selectedTurnIndex
    );
  }

  renderProportionBar(turn) {
    if (!this.contextProportionBar) return;
    this.contextProportionBar.innerHTML = '';

    // Calculate category token aggregates (system, skills, tools, messages, results)
    let sysTokens = 0;
    let skillTokens = 0;
    let toolTokens = 0;
    let histTokens = 0;
    let resTokens = 0;

    const allBlocks = turn.all_blocks || turn.blocks || [
      ...(turn.system_blocks || turn.systemBlocks || []),
      ...(turn.tool_defs || turn.toolDefs || []),
      ...(turn.conversation_history || turn.conversationHistory || []),
      ...(turn.tool_results || turn.toolResults || []),
      ...(turn.assistant_blocks || turn.assistantBlocks || []),
    ];

    if (allBlocks.length > 0) {
      allBlocks.forEach((b) => {
        const count = b.token_count ?? b.tokenCount ?? 0;
        if (this._isSkillBlock(b)) {
          skillTokens += count;
        } else if (this._isSystemBlock(b)) {
          sysTokens += count;
        } else if (this._isToolDefBlock(b)) {
          toolTokens += count;
        } else if (this._isToolResultBlock(b) || this._isToolCallBlock(b)) {
          resTokens += count;
        } else {
          histTokens += count;
        }
      });
    }

    // Fallback to category_breakdown if still 0
    if (sysTokens === 0 && skillTokens === 0 && toolTokens === 0 && histTokens === 0 && resTokens === 0) {
      const cb = turn.category_breakdown || turn.categoryBreakdown || turn.token_breakdown || turn.tokenBreakdown;
      if (cb) {
        sysTokens = cb.system || 0;
        skillTokens = cb.skills || 0;
        toolTokens = cb.tools || cb.tool_defs || 0;
        histTokens = cb.history || cb.conversation_history || cb.conversation || 0;
        resTokens = cb.tool_results || cb.toolResults || cb.results || 0;
      }
    }

    const totalTokens = sysTokens + skillTokens + toolTokens + histTokens + resTokens;

    if (totalTokens === 0) {
      this.contextProportionBar.innerHTML = `
        <div class="proportion-segment empty-bar" style="width: 100%; justify-content: center;">
          No context blocks for this turn
        </div>
      `;
      return;
    }

    const categories = [
      { key: 'system', name: 'System', tokens: sysTokens, class: 'segment-system', filter: 'SYSTEM' },
      { key: 'skills', name: 'Skills', tokens: skillTokens, class: 'segment-skills', filter: 'SKILLS' },
      { key: 'tools', name: 'Tool Defs', tokens: toolTokens, class: 'segment-tools', filter: 'TOOLS' },
      { key: 'messages', name: 'Messages', tokens: histTokens, class: 'segment-messages', filter: 'MESSAGES' },
      { key: 'results', name: 'Tool Executions', tokens: resTokens, class: 'segment-results', filter: 'TOOL_RESULTS' },
    ];

    categories.forEach((cat) => {
      if (cat.tokens <= 0) return;
      const pct = (cat.tokens / totalTokens) * 100;
      const segment = document.createElement('div');
      segment.className = `proportion-segment ${cat.class}`;
      segment.style.width = `${pct}%`;
      segment.title = `${cat.name}: ${cat.tokens.toLocaleString()} tok (${pct.toFixed(1)}%) — Click to filter`;

      const label = document.createElement('span');
      label.textContent = `${cat.name}: ${cat.tokens.toLocaleString()} (${pct.toFixed(0)}%)`;
      segment.appendChild(label);

      segment.addEventListener('click', () => {
        this.setBlockFilter(cat.filter);
      });

      this.contextProportionBar.appendChild(segment);
    });
  }

  selectTurn(turnIndex, userAction = false) {
    this.selectedTurnIndex = turnIndex;
    if (this.turns && this.turns.length > 0) {
      const lastTurn = this.turns[this.turns.length - 1];
      const lastIdx =
        lastTurn.turn_index !== undefined
          ? lastTurn.turn_index
          : lastTurn.turnIndex !== undefined
          ? lastTurn.turnIndex
          : this.turns.length - 1;
      if (userAction) {
        this._userPinnedHistoricalTurn = (turnIndex !== lastIdx);
      }
    }

    const turn = this.turns.find(
      (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === turnIndex
    );
    if (!turn) return;

    const tIdx = turn.turn_index !== undefined ? turn.turn_index : turn.turnIndex;
    if (this.turnTitle) {
      this.turnTitle.textContent = `Turn #${Number(tIdx) + 1} Inspector`;
    }

    this._updateTurnNavControls();
    this.renderContextCapacity();

    if (this.turnMetaRibbon) {
      const inp = (turn.input_tokens ?? turn.inputTokens ?? 0).toLocaleString();
      const out = (turn.output_tokens ?? turn.outputTokens ?? 0).toLocaleString();
      const cached = (
        turn.cached_read_tokens ??
        turn.cachedReadTokens ??
        (turn.cache && (turn.cache.readTokens ?? turn.cache.read_tokens)) ??
        0
      ).toLocaleString();
      const durVal = turn.duration_ms ?? turn.durationMs;
      const dur = durVal !== undefined && durVal !== null ? `${Number(durVal).toFixed(0)}ms` : '—';
      const ttftVal = turn.ttft_ms ?? turn.ttftMs;
      const ttft = ttftVal !== undefined && ttftVal !== null ? `${Number(ttftVal).toFixed(0)}ms` : '—';
      const costVal = turn.turn_cost_usd ?? turn.turnCostUSD;
      const cost = costVal !== undefined && costVal !== null ? `$${Number(costVal).toFixed(4)}` : '$0.0000';

      this.turnMetaRibbon.innerHTML = `
        <div class="turn-meta-item"><span class="label">Input:</span><span class="val">${inp} tok</span></div>
        <div class="turn-meta-item"><span class="label">Output:</span><span class="val">${out} tok</span></div>
        <div class="turn-meta-item"><span class="label">Cached Read:</span><span class="val">${cached} tok</span></div>
        <div class="turn-meta-item"><span class="label">Duration:</span><span class="val">${dur}</span></div>
        <div class="turn-meta-item"><span class="label">TTFT:</span><span class="val">${ttft}</span></div>
        <div class="turn-meta-item"><span class="label">Turn Cost:</span><span class="val">${cost}</span></div>
      `;
    }

    this.renderProportionBar(turn);
    this.renderBlocksTable(turn);
    this.renderRecommendations();

    // Auto-diff (N vs N-1) handling
    if (tIdx === 0 || this.turns.length <= 1) {
      if (this.autoDiffRibbon) {
        this.autoDiffRibbon.innerHTML = `
          <span class="badge badge-info">Turn #1: Initial Prompt Baseline (All blocks initial load)</span>
        `;
      }
    } else {
      this.fetchAutoDiff(tIdx);
    }
  }

  async fetchAutoDiff(turnIndex) {
    if (!this.autoDiffRibbon || !this.activeSessionId) return;

    const prevIndex = turnIndex - 1;
    this._activeDiffTurnIndex = turnIndex;
    this.autoDiffRibbon.innerHTML = `
      <span style="color: var(--text-secondary); font-size: 11px;">
        Comparing Turn #${prevIndex + 1} → Turn #${turnIndex + 1}...
      </span>
    `;

    try {
      const res = await fetch(
        `/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/diff/${prevIndex}/${turnIndex}`
      );
      if (this._activeDiffTurnIndex !== turnIndex || this.selectedTurnIndex !== turnIndex) {
        return;
      }
      if (res.ok) {
        const data = await res.json();
        this.renderAutoDiffRibbon(data, prevIndex, turnIndex);
      } else {
        const err = await res.json().catch(() => ({}));
        this.autoDiffRibbon.innerHTML = `
          <span style="color: var(--color-critical); font-size: 11px;">
            Auto-diff unavailable: ${err.detail || "Diff request failed"}
          </span>
        `;
      }
    } catch (err) {
      if (this._activeDiffTurnIndex !== turnIndex || this.selectedTurnIndex !== turnIndex) {
        return;
      }
      console.error("[DashboardApp] Auto-diff failed:", err);
      this.autoDiffRibbon.innerHTML = `
        <span style="color: var(--color-critical); font-size: 11px;">
          Auto-diff calculation failed
        </span>
      `;
    }
  }

  renderAutoDiffRibbon(data, prevIndex, turnIndex) {
    if (!this.autoDiffRibbon) return;

    const growth = data.tokenGrowth ?? data.token_growth ?? 0;
    let deltaFormatted = "";
    let deltaClass = "";

    if (growth > 0) {
      deltaFormatted = `▲ +${growth.toLocaleString()} tokens`;
      deltaClass = "delta-positive";
    } else if (growth < 0) {
      deltaFormatted = `▼ ${growth.toLocaleString()} tokens`;
      deltaClass = "delta-negative";
    } else {
      deltaFormatted = `±0 tokens`;
      deltaClass = "delta-neutral";
    }

    const added = data.addedBlockIds || data.added_block_ids || [];
    const mutated = data.mutatedBlockIds || data.mutated_block_ids || [];
    const evicted = data.removedBlockIds || data.removed_block_ids || [];
    const persisted = data.persistedBlockIds || data.persisted_block_ids || [];
    const breakpoint = data.cacheBreakpointBlockId || data.cache_breakpoint_block_id;

    const breakpointHtml = breakpoint
      ? `<span class="breakpoint-callout">⚡ Cache Breakpoint at [${breakpoint}]: Subsequent blocks re-tokenized</span>`
      : `<span class="badge badge-success">✓ Prefix Cache Intact</span>`;

    this.autoDiffRibbon.innerHTML = `
      <span class="auto-diff-title">Turn #${prevIndex + 1} → #${turnIndex + 1} Delta:</span>
      <span class="delta-pill ${deltaClass}">${deltaFormatted}</span>
      <span class="badge badge-added" title="${added.length ? added.join(", ") : "None"}">${added.length} Added</span>
      <span class="badge badge-mutated" title="${mutated.length ? mutated.join(", ") : "None"}">${mutated.length} Mutated</span>
      <span class="badge badge-evicted" title="${evicted.length ? evicted.join(", ") : "None"}">${evicted.length} Evicted</span>
      <span class="badge badge-persisted" title="${persisted.length ? persisted.join(", ") : "None"}">${persisted.length} Persisted</span>
      ${breakpointHtml}
    `;
  }

  _escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  _isSkillBlock(block) {
    if (!block) return false;
    const bType = (block.block_type || block.blockType || '').toLowerCase();
    const meta = block.metadata || {};
    const bId = (block.block_id || block.blockId || '').toLowerCase();
    const idKey = (block.identity_key || block.identityKey || '').toLowerCase();
    return (
      bType === 'skill' ||
      bType === 'skills' ||
      meta.type === 'skill' ||
      meta.category === 'skill' ||
      bId.startsWith('blk-skill-') ||
      bId.includes('skill') ||
      idKey.startsWith('skill:')
    );
  }

  _isSystemBlock(block) {
    if (!block) return false;
    if (this._isSkillBlock(block)) return false;
    const bType = (block.block_type || block.blockType || '').toLowerCase();
    const meta = block.metadata || {};
    const bId = (block.block_id || block.blockId || '').toLowerCase();
    const idKey = (block.identity_key || block.identityKey || '').toLowerCase();
    return (
      bType === 'system' ||
      meta.role === 'system' ||
      bId.startsWith('blk-sys-') ||
      bId.includes('system') ||
      idKey.startsWith('system:')
    );
  }

  _isToolDefBlock(block) {
    if (!block) return false;
    const bType = (block.block_type || block.blockType || '').toLowerCase();
    const bId = (block.block_id || block.blockId || '').toLowerCase();
    const idKey = (block.identity_key || block.identityKey || '').toLowerCase();
    return (
      bType === 'tool_def' ||
      bType === 'tool_defs' ||
      bType === 'tool_declaration' ||
      bType === 'tool_declarations' ||
      bId.startsWith('blk-tool-') ||
      bId.includes('tool_def') ||
      bId.includes('tool-schema') ||
      idKey.startsWith('tools:') ||
      idKey.startsWith('tool_def:')
    );
  }

  _isToolResultBlock(block) {
    if (!block) return false;
    const bType = (block.block_type || block.blockType || '').toLowerCase();
    const bId = (block.block_id || block.blockId || '').toLowerCase();
    const idKey = (block.identity_key || block.identityKey || '').toLowerCase();
    const meta = block.metadata || {};
    return (
      bType === 'tool_result' ||
      bType === 'tool_results' ||
      meta.type === 'tool_result' ||
      meta.category === 'tool_result' ||
      bId.startsWith('blk-result-') ||
      bId.startsWith('tool_res_') ||
      bId.includes('tool_result') ||
      idKey.startsWith('tool_result:')
    );
  }

  _isToolCallBlock(block) {
    if (!block) return false;
    if (
      this._isToolResultBlock(block) ||
      this._isToolDefBlock(block) ||
      this._isSystemBlock(block) ||
      this._isSkillBlock(block)
    ) {
      return false;
    }
    const bType = (block.block_type || block.blockType || '').toLowerCase();
    const bId = (block.block_id || block.blockId || '').toLowerCase();
    const idKey = (block.identity_key || block.identityKey || '').toLowerCase();
    const meta = block.metadata || {};
    const content = block.content;
    const hasCallAction =
      typeof content === 'object' &&
      content !== null &&
      (content.action === 'call' || (content.tool && !content.output && content.arguments !== undefined));
    return (
      bType === 'tool_use' ||
      meta.type === 'tool_use' ||
      meta.tool_use_id !== undefined ||
      meta.tool_call_id !== undefined ||
      bId.includes('_call_') ||
      bId.startsWith('blk-call-') ||
      idKey.includes('tool_call:') ||
      hasCallAction
    );
  }

  _isMessageBlock(block) {
    if (!block) return false;
    if (
      this._isSystemBlock(block) ||
      this._isSkillBlock(block) ||
      this._isToolDefBlock(block) ||
      this._isToolResultBlock(block) ||
      this._isToolCallBlock(block)
    ) {
      return false;
    }
    const bType = (block.block_type || block.blockType || '').toLowerCase();
    const role = (block.metadata && block.metadata.role ? block.metadata.role : '').toLowerCase();
    const bId = (block.block_id || block.blockId || '').toLowerCase();
    return (
      bType === 'user_msg' ||
      bType === 'assistant_msg' ||
      bType === 'conversation_history' ||
      bType === 'thought' ||
      bType === 'user' ||
      bType === 'assistant' ||
      role === 'user' ||
      role === 'assistant' ||
      bId.startsWith('hist_') ||
      bId.startsWith('blk-user-') ||
      bId.startsWith('blk-assistant-') ||
      bId.startsWith('blk-hist-') ||
      bId.startsWith('blk-tht-') ||
      bId.startsWith('blk-asst-')
    );
  }

  _getMessageRole(block) {
    const bType = (block.block_type || block.blockType || '').toLowerCase();
    const role = (block.metadata && block.metadata.role ? block.metadata.role : '').toLowerCase();
    const bId = (block.block_id || block.blockId || '').toLowerCase();

    if (role === 'user' || bType.includes('user') || bId.startsWith('blk-user-')) {
      return 'user';
    }
    if (
      bType === 'thought' ||
      role === 'thought' ||
      role === 'reasoning' ||
      bId.includes('_thought') ||
      bId.startsWith('blk-tht-')
    ) {
      return 'thought';
    }
    if (
      role === 'assistant' ||
      bType.includes('assistant') ||
      bId.startsWith('blk-assistant-') ||
      bId.startsWith('blk-hist-asst-') ||
      bId.startsWith('blk-asst-')
    ) {
      return 'assistant';
    }
    const m = bId.match(/^hist_(\d+)/);
    if (m) {
      const idx = parseInt(m[1], 10);
      return idx % 2 === 0 ? 'user' : 'assistant';
    }
    return 'user';
  }

  _getToolName(block) {
    if (!block) return 'Tool';
    const meta = block.metadata || {};
    if (meta.tool_name) return meta.tool_name;
    if (meta.name) return meta.name;
    if (meta.tool) return meta.tool;
    const content = block.content;
    if (typeof content === 'object' && content !== null) {
      if (content.tool) return content.tool;
      if (content.name) return content.name;
    }
    const idKey = block.identity_key || block.identityKey || '';
    const mKey = idKey.match(/tool(?:_call|_result)?:([a-zA-Z0-9_\-]+)/);
    if (mKey) {
      return mKey[1].replace(/_\d+.*$/, '');
    }
    const bId = block.block_id || block.blockId || '';
    const mId = bId.match(/blk-(?:call|result)-([a-zA-Z0-9_]+)/);
    if (mId) {
      const raw = mId[1];
      if (raw === 'bash') return 'execute_bash';
      if (raw === 'edit') return 'edit_file';
      return raw;
    }
    return 'Tool';
  }

  _getToolCallId(block) {
    if (!block) return '';
    const meta = block.metadata || {};
    if (meta.tool_use_id) return String(meta.tool_use_id);
    if (meta.tool_call_id) return String(meta.tool_call_id);
    if (meta.call_id) return String(meta.call_id);
    const bId = block.block_id || block.blockId || '';
    const mCall = bId.match(/_call_([a-zA-Z0-9_\-]+)/);
    if (mCall) return mCall[1];
    const mRes = bId.match(/tool_res_([a-zA-Z0-9_\-]+)/);
    if (mRes) return mRes[1];
    return '';
  }

  _getSkillName(block) {
    if (!block) return 'Skill';
    const meta = block.metadata || {};
    if (meta.skill_name) return meta.skill_name;
    if (meta.name) return meta.name;
    const idKey = block.identity_key || block.identityKey || '';
    if (idKey.startsWith('skill:')) {
      const parts = idKey.split(':');
      if (parts[1]) {
        return parts[1].replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
      }
    }
    const bId = block.block_id || block.blockId || '';
    const m = bId.match(/blk-skill-([a-zA-Z0-9_\-]+)/);
    if (m) {
      return m[1].replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }
    if (typeof block.content === 'string') {
      const firstLine = block.content.trim().split('\n')[0];
      const headingMatch = firstLine.match(/^#+\s*(?:Skill:)?\s*(.+)$/i);
      if (headingMatch) return headingMatch[1].trim();
    }
    return 'Skill';
  }

  _isToolError(block) {
    if (!block) return false;
    const meta = block.metadata || {};
    if (meta.is_error === true || meta.error === true) return true;
    const content = block.content;
    if (typeof content === 'object' && content !== null) {
      if (content.is_error === true) return true;
    }
    const raw = typeof content === 'string' ? content : JSON.stringify(content || '');
    return /\b(FAILED|Traceback|Error:|Exception:)\b/.test(raw);
  }

  _extractBlockSnippet(block, maxLength = 100) {
    if (!block) return '';
    const content = block.content;
    let rawText = '';
    if (typeof content === 'string') {
      const trimmed = content.trim();
      if (
        (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
        (trimmed.startsWith('[') && trimmed.endsWith(']'))
      ) {
        try {
          const parsed = JSON.parse(trimmed);
          rawText = this._extractTextFromObject(parsed);
        } catch (_) {
          rawText = trimmed;
        }
      } else {
        rawText = trimmed;
      }
    } else if (typeof content === 'object' && content !== null) {
      rawText = this._extractTextFromObject(content);
    }

    if (!rawText && block.metadata) {
      rawText = this._extractTextFromObject(block.metadata);
    }

    const cleaned = String(rawText || '')
      .replace(/[\r\n\t]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();

    if (cleaned.length > maxLength) {
      return cleaned.slice(0, maxLength) + '...';
    }
    return cleaned || '—';
  }

  _extractTextFromObject(obj) {
    if (!obj) return '';
    if (typeof obj === 'string') return obj;
    if (Array.isArray(obj)) {
      const toolNames = obj.map((x) => x && x.name).filter(Boolean);
      if (toolNames.length > 0 && toolNames.length === obj.length) {
        return `Tools: ${toolNames.join(', ')}`;
      }
      return obj
        .map((item) => this._extractTextFromObject(item))
        .filter(Boolean)
        .join(' ');
    }
    if (typeof obj === 'object') {
      if (typeof obj.content === 'string') return obj.content;
      if (Array.isArray(obj.content)) return this._extractTextFromObject(obj.content);
      if (typeof obj.text === 'string') return obj.text;
      if (typeof obj.instructions === 'string') return obj.instructions;
      if (typeof obj.description === 'string') return obj.description;
      if (obj.arguments) {
        const argsStr = typeof obj.arguments === 'string' ? obj.arguments : JSON.stringify(obj.arguments);
        return obj.tool ? `${obj.tool}: ${argsStr}` : argsStr;
      }
      if (obj.action && obj.tool) {
        return `${obj.tool} (${obj.action})`;
      }
      if (obj.command) return String(obj.command);
      if (obj.name) return String(obj.name);
      try {
        return JSON.stringify(obj);
      } catch (_) {
        return String(obj);
      }
    }
    return String(obj);
  }

  renderBlocksTable(turn) {
    if (!this.blocksTableBody) return;
    this.blocksTableBody.innerHTML = '';

    // Collect all context blocks
    let blocks = [];
    if (turn.all_blocks && turn.all_blocks.length > 0) {
      blocks = turn.all_blocks;
    } else if (turn.blocks && turn.blocks.length > 0) {
      blocks = turn.blocks;
    } else {
      blocks = [
        ...(turn.system_blocks || turn.systemBlocks || []),
        ...(turn.tool_defs || turn.toolDefs || []),
        ...(turn.conversation_history || turn.conversationHistory || []),
        ...(turn.tool_results || turn.toolResults || []),
        ...(turn.assistant_blocks || turn.assistantBlocks || []),
      ];
    }

    if (blocks.length === 0) {
      this.blocksTableBody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 24px;">
            No context blocks found for this turn.
          </td>
        </tr>
      `;
      return;
    }

    // Calculate total turn tokens across all blocks for relative share
    const totalTurnTokens =
      blocks.reduce(
        (acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0),
        0
      ) || (turn.input_tokens ?? turn.inputTokens ?? 0);

    // Filter rows based on this.currentBlockFilter
    const filter = this.currentBlockFilter || 'ALL';
    const matchesFilter = (b) => {
      const status = (b.lifecycle_status || b.status || '').toLowerCase();
      switch (filter) {
        case 'SYSTEM':
          return this._isSystemBlock(b);
        case 'SKILLS':
          return this._isSkillBlock(b);
        case 'TOOLS':
          return this._isToolDefBlock(b);
        case 'MESSAGES':
          return this._isMessageBlock(b);
        case 'TOOL_RESULTS':
          return this._isToolResultBlock(b) || this._isToolCallBlock(b);
        case 'ADDED':
          return status === 'added';
        case 'MUTATED':
          return status === 'mutated';
        case 'ALL':
        default:
          return true;
      }
    };

    const filteredBlocks = blocks.filter(matchesFilter);

    if (filteredBlocks.length === 0) {
      this.blocksTableBody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 24px;">
            No context blocks matching filter "${filter}".
          </td>
        </tr>
      `;
      return;
    }

    // Helper to format token display
    const formatTokenDisplay = (tokCount) => {
      const pctNum = totalTurnTokens > 0 ? (tokCount / totalTurnTokens) * 100 : 0;
      let pctStr = '0%';
      if (pctNum >= 1) {
        pctStr = `${Math.round(pctNum)}%`;
      } else if (pctNum > 0) {
        pctStr = '<1%';
      }
      return `${tokCount.toLocaleString()} tok (${pctStr})`;
    };

    // Helper to build lifecycle status badge
    const getStatusBadge = (status) => {
      if (status === 'added') {
        return '<span class="badge badge-added" style="margin-left: 6px;">[+] Added</span>';
      } else if (status === 'mutated') {
        return '<span class="badge badge-mutated" style="margin-left: 6px;">[~] Mutated</span>';
      } else if (status === 'evicted') {
        return '<span class="badge badge-evicted" style="margin-left: 6px;">[-] Evicted</span>';
      } else if (status === 'persisted') {
        return '<span class="badge badge-persisted" style="margin-left: 6px;">[=] Persisted</span>';
      }
      return '';
    };

    // Helper to render section group header
    const renderSectionHeader = (sectionKey, title, subLabel, badgeText, tokens, maxSurvived = 0, sectionClass = '') => {
      const isCollapsed = this.collapsedSections.has(sectionKey);
      const pctNum = totalTurnTokens > 0 ? (tokens / totalTurnTokens) * 100 : 0;
      const pctStr = pctNum >= 1 ? `${Math.round(pctNum)}%` : pctNum > 0 ? '<1%' : '0%';

      const headerRow = document.createElement('tr');
      headerRow.className = `section-group-header ${isCollapsed ? 'collapsed' : ''} ${sectionClass}`;
      headerRow.dataset.section = sectionKey;

      headerRow.innerHTML = `
        <td colspan="6">
          <div class="exchange-header-content">
            <div class="exchange-header-title">
              <span class="exchange-collapse-icon">▼</span>
              <span class="exchange-badge">${this._escapeHtml(title)}</span>
              ${subLabel ? `<span class="exchange-turn-label">${this._escapeHtml(subLabel)}</span>` : ''}
            </div>
            <div class="exchange-header-metrics">
              <span class="badge badge-info">${this._escapeHtml(badgeText)}</span>
              <span class="exchange-tokens">${tokens.toLocaleString()} tok (${pctStr})</span>
              ${maxSurvived > 0 ? `<span class="exchange-survived">${maxSurvived} turns survived</span>` : ''}
            </div>
          </div>
        </td>
      `;

      headerRow.addEventListener('click', () => {
        const willCollapse = !this.collapsedSections.has(sectionKey);
        if (willCollapse) {
          this.collapsedSections.add(sectionKey);
          headerRow.classList.add('collapsed');
        } else {
          this.collapsedSections.delete(sectionKey);
          headerRow.classList.remove('collapsed');
        }
        const itemRows = this.blocksTableBody.querySelectorAll(`.exchange-item-row[data-section="${sectionKey}"]`);
        itemRows.forEach((r) => {
          if (willCollapse) {
            r.classList.add('exchange-hidden');
          } else {
            r.classList.remove('exchange-hidden');
          }
        });
      });

      this.blocksTableBody.appendChild(headerRow);
    };

    // Helper to render standard context row (System, Skill, Tool Defs)
    const createStandardRow = (b, opts) => {
      const row = document.createElement('tr');
      const bId = b.block_id || b.blockId || '—';
      row.dataset.blockId = bId;
      row.dataset.section = opts.sectionKey;
      let rowClass = `exchange-item-row ${opts.rowClass}`;
      if (opts.isCollapsed) {
        rowClass += ' exchange-hidden';
      }
      row.className = rowClass;

      const identityKey = b.identity_key || b.identityKey || '';
      const status = b.lifecycle_status || b.status || '';
      const tokCount = b.token_count ?? b.tokenCount ?? 0;
      const hash = b.content_hash || b.contentHash || '';
      const hashShort = hash ? `${hash.slice(0, 8)}...` : '—';
      const survived = b.turns_survived ?? b.turnsSurvived;
      const survivedText = survived !== undefined ? `${survived} turns` : '—';
      const snippet = this._extractBlockSnippet(b, 100);
      const fullSnippet = this._extractBlockSnippet(b, 600);

      row.innerHTML = `
        <td class="code-cell">
          <div class="msg-block-identity">
            <span class="msg-role-pill ${opts.rolePillClass}">${this._escapeHtml(opts.roleLabel)}</span>
            <span class="msg-block-subid">${this._escapeHtml(bId)}</span>
          </div>
          <div class="msg-snippet-box" title="${this._escapeHtml(fullSnippet)}">&ldquo;${this._escapeHtml(snippet)}&rdquo;</div>
          ${identityKey ? `<div class="msg-identity-sub">${this._escapeHtml(identityKey)}</div>` : ''}
        </td>
        <td>
          <span class="badge ${opts.badgeClass}">${this._escapeHtml(opts.badgeLabel)}</span>
          ${getStatusBadge(status)}
        </td>
        <td style="font-family: var(--font-mono); white-space: nowrap;">${formatTokenDisplay(tokCount)}</td>
        <td>${survivedText}</td>
        <td class="hash-cell">${hashShort}</td>
        <td>
          <button class="btn" style="padding: 2px 8px; font-size: 11px;">View Content</button>
        </td>
      `;

      const viewBtn = row.querySelector('button');
      if (viewBtn) {
        viewBtn.addEventListener('click', () => {
          this.openModal(`Block: ${bId} (${opts.badgeLabel})`, b.content || JSON.stringify(b, null, 2));
        });
      }
      return row;
    };

    // Helper to render conversation message item row within an exchange
    const createMessageRow = (b, exIdx, isCollapsed) => {
      const row = document.createElement('tr');
      const bId = b.block_id || b.blockId || '—';
      row.dataset.blockId = bId;
      row.dataset.exchange = String(exIdx);
      row.dataset.section = 'messages';
      const role = this._getMessageRole(b);

      let rowClass = 'exchange-item-row';
      let rolePillClass = 'role-user';
      let roleLabel = '👤 User Prompt';
      let badgeLabel = 'User Message';
      let badgeClass = 'badge-role-user';

      if (role === 'thought') {
        rowClass += ' thought-msg-row';
        rolePillClass = 'role-thought';
        roleLabel = '💭 Reasoning';
        badgeLabel = 'Reasoning';
        badgeClass = 'badge-role-thought';
      } else if (role === 'assistant') {
        rowClass += ' assistant-msg-row';
        rolePillClass = 'role-assistant';
        roleLabel = '🤖 Assistant Response';
        badgeLabel = 'Assistant Message';
        badgeClass = 'badge-role-assistant';
      } else {
        rowClass += ' user-msg-row';
      }

      if (isCollapsed || this.collapsedSections.has('messages')) {
        rowClass += ' exchange-hidden';
      }
      row.className = rowClass;

      const identityKey = b.identity_key || b.identityKey || '';
      const status = b.lifecycle_status || b.status || '';
      const tokCount = b.token_count ?? b.tokenCount ?? 0;
      const hash = b.content_hash || b.contentHash || '';
      const hashShort = hash ? `${hash.slice(0, 8)}...` : '—';
      const survived = b.turns_survived ?? b.turnsSurvived;
      const survivedText = survived !== undefined ? `${survived} turns` : '—';
      const snippet = this._extractBlockSnippet(b, 100);
      const fullSnippet = this._extractBlockSnippet(b, 600);

      row.innerHTML = `
        <td class="code-cell">
          <div class="msg-block-identity">
            <span class="msg-role-pill ${rolePillClass}">${roleLabel}</span>
            <span class="msg-block-subid">${this._escapeHtml(bId)}</span>
          </div>
          <div class="msg-snippet-box" title="${this._escapeHtml(fullSnippet)}">&ldquo;${this._escapeHtml(snippet)}&rdquo;</div>
          ${identityKey ? `<div class="msg-identity-sub">${this._escapeHtml(identityKey)}</div>` : ''}
        </td>
        <td>
          <span class="badge ${badgeClass}">${badgeLabel}</span>
          ${getStatusBadge(status)}
        </td>
        <td style="font-family: var(--font-mono); white-space: nowrap;">${formatTokenDisplay(tokCount)}</td>
        <td>${survivedText}</td>
        <td class="hash-cell">${hashShort}</td>
        <td>
          <button class="btn" style="padding: 2px 8px; font-size: 11px;">View Content</button>
        </td>
      `;

      const viewBtn = row.querySelector('button');
      if (viewBtn) {
        viewBtn.addEventListener('click', () => {
          this.openModal(`Block: ${bId} (${badgeLabel})`, b.content || JSON.stringify(b, null, 2));
        });
      }
      return row;
    };

    // Helper to render entire conversation exchange
    const renderExchange = (exchange) => {
      const exIdx = exchange.exchangeIndex;
      const isCollapsed = this.collapsedExchanges.has(String(exIdx)) || this.collapsedSections.has('messages');
      const exPctNum = totalTurnTokens > 0 ? (exchange.totalTokens / totalTurnTokens) * 100 : 0;
      const exPctStr = exPctNum >= 1 ? `${Math.round(exPctNum)}%` : exPctNum > 0 ? '<1%' : '0%';

      const headerRow = document.createElement('tr');
      headerRow.className = `exchange-group-header ${isCollapsed ? 'collapsed' : ''}`;
      headerRow.dataset.exchange = String(exIdx);
      headerRow.dataset.section = 'messages';

      headerRow.innerHTML = `
        <td colspan="6">
          <div class="exchange-header-content">
            <div class="exchange-header-title">
              <span class="exchange-collapse-icon">▼</span>
              <span class="exchange-badge">${this._escapeHtml(exchange.title)}</span>
              <span class="exchange-turn-label">${this._escapeHtml(exchange.subLabel)}</span>
            </div>
            <div class="exchange-header-metrics">
              <span class="badge badge-info">${exchange.blocks.length} msg${exchange.blocks.length === 1 ? '' : 's'}</span>
              <span class="exchange-tokens">${exchange.totalTokens.toLocaleString()} tok (${exPctStr})</span>
              ${exchange.maxSurvived > 0 ? `<span class="exchange-survived">${exchange.maxSurvived} turns survived</span>` : ''}
            </div>
          </div>
        </td>
      `;

      headerRow.addEventListener('click', () => {
        const key = String(exIdx);
        const willCollapse = !this.collapsedExchanges.has(key);
        if (willCollapse) {
          this.collapsedExchanges.add(key);
          headerRow.classList.add('collapsed');
        } else {
          this.collapsedExchanges.delete(key);
          headerRow.classList.remove('collapsed');
        }
        const itemRows = this.blocksTableBody.querySelectorAll(`.exchange-item-row[data-exchange="${key}"]`);
        itemRows.forEach((r) => {
          if (willCollapse) {
            r.classList.add('exchange-hidden');
          } else {
            r.classList.remove('exchange-hidden');
          }
        });
      });

      this.blocksTableBody.appendChild(headerRow);

      exchange.blocks.forEach((b) => {
        const row = createMessageRow(b, exIdx, isCollapsed);
        this.blocksTableBody.appendChild(row);
      });
    };

    // Partition filtered blocks into structured sections
    const sysBlocks = filteredBlocks.filter((b) => this._isSystemBlock(b));
    const skillBlocks = filteredBlocks.filter((b) => this._isSkillBlock(b));
    const toolDefBlocks = filteredBlocks.filter((b) => this._isToolDefBlock(b));
    const toolCallBlocks = filteredBlocks.filter((b) => this._isToolCallBlock(b));
    const toolResultBlocks = filteredBlocks.filter((b) => this._isToolResultBlock(b));
    const messageBlocks = filteredBlocks.filter((b) => this._isMessageBlock(b));

    // SECTION 1: System Instructions
    if (sysBlocks.length > 0) {
      const sysTokens = sysBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      const maxSurv = Math.max(0, ...sysBlocks.map((b) => b.turns_survived ?? b.turnsSurvived ?? 0));
      renderSectionHeader(
        'system',
        '⚙️ System Instructions',
        'Core Agent Prompts & Context Rules',
        `${sysBlocks.length} block${sysBlocks.length === 1 ? '' : 's'}`,
        sysTokens,
        maxSurv,
        'section-system-header'
      );
      const isSecCollapsed = this.collapsedSections.has('system');
      sysBlocks.forEach((b) => {
        const row = createStandardRow(b, {
          sectionKey: 'system',
          rowClass: 'system-row',
          rolePillClass: 'role-system',
          roleLabel: '⚙️ System Prompt',
          badgeClass: 'badge-role-system',
          badgeLabel: 'System Prompt',
          isCollapsed: isSecCollapsed,
        });
        this.blocksTableBody.appendChild(row);
      });
    }

    // SECTION 2: Agent Skills & Standards
    if (skillBlocks.length > 0) {
      const skillTokens = skillBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      const maxSurv = Math.max(0, ...skillBlocks.map((b) => b.turns_survived ?? b.turnsSurvived ?? 0));
      renderSectionHeader(
        'skills',
        '🎯 Agent Skills & Standards',
        'Injected Skills & Coding Guidelines',
        `${skillBlocks.length} skill${skillBlocks.length === 1 ? '' : 's'}`,
        skillTokens,
        maxSurv,
        'section-skills-header'
      );
      const isSecCollapsed = this.collapsedSections.has('skills');
      skillBlocks.forEach((b) => {
        const skillName = this._getSkillName(b);
        const row = createStandardRow(b, {
          sectionKey: 'skills',
          rowClass: 'skill-row',
          rolePillClass: 'role-skill',
          roleLabel: `🎯 Skill: ${skillName}`,
          badgeClass: 'badge-role-skill',
          badgeLabel: 'Agent Skill',
          isCollapsed: isSecCollapsed,
        });
        this.blocksTableBody.appendChild(row);
      });
    }

    // SECTION 3: Tool Definitions & Schemas
    if (toolDefBlocks.length > 0) {
      const toolDefTokens = toolDefBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      const maxSurv = Math.max(0, ...toolDefBlocks.map((b) => b.turns_survived ?? b.turnsSurvived ?? 0));
      let toolNamesCount = 0;
      toolDefBlocks.forEach((b) => {
        if (Array.isArray(b.content)) toolNamesCount += b.content.length;
        else if (b.content && typeof b.content === 'object' && b.content.tools && Array.isArray(b.content.tools)) {
          toolNamesCount += b.content.tools.length;
        } else {
          toolNamesCount += 1;
        }
      });
      renderSectionHeader(
        'tools',
        '🔧 Tool Definitions & Schemas',
        'Registered Tool APIs & Specs',
        `${toolDefBlocks.length} schema block${toolDefBlocks.length === 1 ? '' : 's'}${toolNamesCount > 0 ? ` (${toolNamesCount} tools)` : ''}`,
        toolDefTokens,
        maxSurv,
        'section-tools-header'
      );
      const isSecCollapsed = this.collapsedSections.has('tools');
      toolDefBlocks.forEach((b) => {
        const row = createStandardRow(b, {
          sectionKey: 'tools',
          rowClass: 'tool-def-row',
          rolePillClass: 'role-tool',
          roleLabel: '🔧 Tool Schemas',
          badgeClass: 'badge-role-tool',
          badgeLabel: 'Tool Definition',
          isCollapsed: isSecCollapsed,
        });
        this.blocksTableBody.appendChild(row);
      });
    }

    // SECTION 4: Conversation Exchanges
    if (messageBlocks.length > 0) {
      let exchangeCounter = 0;
      const exchanges = [];
      let currentEx = null;

      messageBlocks.forEach((b) => {
        const role = this._getMessageRole(b);
        const bId = b.block_id || b.blockId || '';
        const m = bId.match(/^hist_(\d+)/);
        const msgIdx = m ? parseInt(m[1], 10) : null;

        let startNew = false;
        if (!currentEx) {
          startNew = true;
        } else if (role === 'user') {
          if (msgIdx !== null && currentEx.lastMsgIdx === msgIdx) {
            startNew = false;
          } else {
            startNew = true;
          }
        }

        if (startNew) {
          exchangeCounter += 1;
          currentEx = {
            exchangeIndex: exchangeCounter,
            blocks: [],
            totalTokens: 0,
            maxSurvived: 0,
            hasUser: false,
            hasAssistant: false,
            lastMsgIdx: msgIdx,
          };
          exchanges.push(currentEx);
        }

        currentEx.blocks.push(b);
        currentEx.lastMsgIdx = msgIdx;
        const tok = b.token_count ?? b.tokenCount ?? 0;
        currentEx.totalTokens += tok;
        const surv = b.turns_survived ?? b.turnsSurvived ?? 0;
        if (surv > currentEx.maxSurvived) {
          currentEx.maxSurvived = surv;
        }
        if (role === 'user') currentEx.hasUser = true;
        if (role === 'assistant' || role === 'thought') currentEx.hasAssistant = true;
      });

      exchanges.forEach((ex, i) => {
        const isLast = i === exchanges.length - 1;
        if (isLast && ex.hasUser && !ex.hasAssistant) {
          const tIdx = turn.turn_index !== undefined ? turn.turn_index : turn.turnIndex;
          ex.title = `💬 Current Turn (${tIdx !== undefined ? `#${tIdx}` : 'Active'}) Prompt`;
          ex.subLabel = 'Active User Input';
        } else {
          ex.title = `💬 Conversation Exchange #${ex.exchangeIndex}`;
          ex.subLabel = `Turn ${ex.exchangeIndex - 1}`;
        }
        renderExchange(ex);
      });
    }

    // SECTION 5: Tool Executions & Output Payloads (Call ➔ Result linking)
    if (toolCallBlocks.length > 0 || toolResultBlocks.length > 0) {
      const execItems = [];
      const usedCallIds = new Set();
      const usedResultIds = new Set();

      // 1. Exact ID matching (tool_use_id / tool_call_id / call_id)
      toolCallBlocks.forEach((call) => {
        const callId = this._getToolCallId(call);
        if (!callId) return;
        const matchRes = toolResultBlocks.find((res) => {
          const resId = this._getToolCallId(res);
          return resId && resId === callId && !usedResultIds.has(res.block_id || res.blockId);
        });
        if (matchRes) {
          usedCallIds.add(call.block_id || call.blockId);
          usedResultIds.add(matchRes.block_id || matchRes.blockId);
          execItems.push({
            callBlock: call,
            resultBlock: matchRes,
            toolName: this._getToolName(call) || this._getToolName(matchRes),
            callId: callId,
            totalTokens:
              (call.token_count ?? call.tokenCount ?? 0) + (matchRes.token_count ?? matchRes.tokenCount ?? 0),
            isError: this._isToolError(matchRes),
          });
        }
      });

      // 2. Tool name-based matching for remaining unmatched in turn sequence
      toolCallBlocks.forEach((call) => {
        const cId = call.block_id || call.blockId;
        if (usedCallIds.has(cId)) return;
        const callToolName = this._getToolName(call);
        const matchRes = toolResultBlocks.find((res) => {
          const rId = res.block_id || res.blockId;
          if (usedResultIds.has(rId)) return false;
          return this._getToolName(res) === callToolName;
        });
        if (matchRes) {
          usedCallIds.add(cId);
          usedResultIds.add(matchRes.block_id || matchRes.blockId);
          execItems.push({
            callBlock: call,
            resultBlock: matchRes,
            toolName: callToolName,
            callId: this._getToolCallId(call) || this._getToolCallId(matchRes),
            totalTokens:
              (call.token_count ?? call.tokenCount ?? 0) + (matchRes.token_count ?? matchRes.tokenCount ?? 0),
            isError: this._isToolError(matchRes),
          });
        }
      });

      // 3. Unmatched Tool Results (e.g. executed from prior turn's call)
      toolResultBlocks.forEach((res) => {
        const rId = res.block_id || res.blockId;
        if (usedResultIds.has(rId)) return;
        execItems.push({
          callBlock: null,
          resultBlock: res,
          toolName: this._getToolName(res),
          callId: this._getToolCallId(res),
          totalTokens: res.token_count ?? res.tokenCount ?? 0,
          isError: this._isToolError(res),
        });
      });

      // 4. Unmatched Tool Calls (e.g. pending assistant call in current turn)
      toolCallBlocks.forEach((call) => {
        const cId = call.block_id || call.blockId;
        if (usedCallIds.has(cId)) return;
        execItems.push({
          callBlock: call,
          resultBlock: null,
          toolName: this._getToolName(call),
          callId: this._getToolCallId(call),
          totalTokens: call.token_count ?? call.tokenCount ?? 0,
          isError: false,
        });
      });

      const execTokens = [...toolCallBlocks, ...toolResultBlocks].reduce(
        (acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0),
        0
      );
      const maxSurv = Math.max(
        0,
        ...[...toolCallBlocks, ...toolResultBlocks].map((b) => b.turns_survived ?? b.turnsSurvived ?? 0)
      );

      renderSectionHeader(
        'executions',
        '⚡ Tool Executions & Output Payloads',
        'Linked Tool Invocations & Execution Payloads',
        `${execItems.length} execution${execItems.length === 1 ? '' : 's'}`,
        execTokens,
        maxSurv,
        'section-executions-header'
      );

      const isSecCollapsed = this.collapsedSections.has('executions');

      execItems.forEach((item) => {
        // Render call row
        if (item.callBlock) {
          const callRow = document.createElement('tr');
          const b = item.callBlock;
          const bId = b.block_id || b.blockId || '—';
          callRow.dataset.blockId = bId;
          callRow.dataset.section = 'executions';
          let rowClass = 'exchange-item-row tool-call-row';
          if (isSecCollapsed) rowClass += ' exchange-hidden';
          callRow.className = rowClass;

          const identityKey = b.identity_key || b.identityKey || '';
          const status = b.lifecycle_status || b.status || '';
          const tokCount = b.token_count ?? b.tokenCount ?? 0;
          const hash = b.content_hash || b.contentHash || '';
          const hashShort = hash ? `${hash.slice(0, 8)}...` : '—';
          const survived = b.turns_survived ?? b.turnsSurvived;
          const survivedText = survived !== undefined ? `${survived} turns` : '—';
          const snippet = this._extractBlockSnippet(b, 100);
          const fullSnippet = this._extractBlockSnippet(b, 600);

          callRow.innerHTML = `
            <td class="code-cell">
              <div class="msg-block-identity">
                <span class="msg-role-pill role-tool-call">🤖 Tool Call: ${this._escapeHtml(item.toolName)}</span>
                <span class="msg-block-subid">${this._escapeHtml(bId)}${item.callId ? ` [id: ${this._escapeHtml(item.callId)}]` : ''}</span>
              </div>
              <div class="msg-snippet-box" title="${this._escapeHtml(fullSnippet)}">&ldquo;${this._escapeHtml(snippet)}&rdquo;</div>
              ${identityKey ? `<div class="msg-identity-sub">${this._escapeHtml(identityKey)}</div>` : ''}
            </td>
            <td>
              <span class="badge badge-role-tool-call">Tool Call</span>
              ${getStatusBadge(status)}
            </td>
            <td style="font-family: var(--font-mono); white-space: nowrap;">${formatTokenDisplay(tokCount)}</td>
            <td>${survivedText}</td>
            <td class="hash-cell">${hashShort}</td>
            <td>
              <button class="btn" style="padding: 2px 8px; font-size: 11px;">View Content</button>
            </td>
          `;

          const viewBtn = callRow.querySelector('button');
          if (viewBtn) {
            viewBtn.addEventListener('click', () => {
              this.openModal(`Tool Call: ${item.toolName} (${bId})`, b.content || JSON.stringify(b, null, 2));
            });
          }
          this.blocksTableBody.appendChild(callRow);
        }

        // Render result row
        if (item.resultBlock) {
          const resultRow = document.createElement('tr');
          const b = item.resultBlock;
          const bId = b.block_id || b.blockId || '—';
          resultRow.dataset.blockId = bId;
          resultRow.dataset.section = 'executions';
          let rowClass = `exchange-item-row tool-result-row ${item.isError ? 'tool-error-row' : ''}`;
          if (isSecCollapsed) rowClass += ' exchange-hidden';
          resultRow.className = rowClass;

          const identityKey = b.identity_key || b.identityKey || '';
          const status = b.lifecycle_status || b.status || '';
          const tokCount = b.token_count ?? b.tokenCount ?? 0;
          const hash = b.content_hash || b.contentHash || '';
          const hashShort = hash ? `${hash.slice(0, 8)}...` : '—';
          const survived = b.turns_survived ?? b.turnsSurvived;
          const survivedText = survived !== undefined ? `${survived} turns` : '—';
          const snippet = this._extractBlockSnippet(b, 100);
          const fullSnippet = this._extractBlockSnippet(b, 600);

          const rolePillLabel = item.isError
            ? `❌ Tool Output: ${this._escapeHtml(item.toolName)} (Failed)`
            : `⚡ Tool Output: ${this._escapeHtml(item.toolName)}`;
          const badgeClass = item.isError ? 'badge-role-tool-error' : 'badge-role-tool-result';
          const badgeLabel = item.isError ? 'Tool Error' : 'Tool Result';

          resultRow.innerHTML = `
            <td class="code-cell">
              <div class="msg-block-identity">
                <span class="msg-role-pill role-tool-result ${item.isError ? 'is-error' : ''}">${rolePillLabel}</span>
                <span class="msg-block-subid">${this._escapeHtml(bId)}${item.callId ? ` [ref: ${this._escapeHtml(item.callId)}]` : ''}</span>
              </div>
              <div class="msg-snippet-box" title="${this._escapeHtml(fullSnippet)}">&ldquo;${this._escapeHtml(snippet)}&rdquo;</div>
              ${identityKey ? `<div class="msg-identity-sub">${this._escapeHtml(identityKey)}</div>` : ''}
            </td>
            <td>
              <span class="badge ${badgeClass}">${badgeLabel}</span>
              ${getStatusBadge(status)}
            </td>
            <td style="font-family: var(--font-mono); white-space: nowrap;">${formatTokenDisplay(tokCount)}</td>
            <td>${survivedText}</td>
            <td class="hash-cell">${hashShort}</td>
            <td>
              <button class="btn" style="padding: 2px 8px; font-size: 11px;">View Content</button>
            </td>
          `;

          const viewBtn = resultRow.querySelector('button');
          if (viewBtn) {
            viewBtn.addEventListener('click', () => {
              this.openModal(`Tool Result: ${item.toolName} (${bId})`, b.content || JSON.stringify(b, null, 2));
            });
          }
          this.blocksTableBody.appendChild(resultRow);
        }
      });
    }
  }

  renderEmptyTurnInspector() {
    if (this.turnTitle) this.turnTitle.textContent = 'Turn Inspector';
    this._updateTurnNavControls();
    if (this.turnMetaRibbon) this.turnMetaRibbon.innerHTML = '<span>Waiting for proxied agent traffic...</span>';
    if (this.autoDiffRibbon) this.autoDiffRibbon.innerHTML = '';
    if (this.contextProportionBar) this.contextProportionBar.innerHTML = '';
    if (this.blocksTableBody) {
      this.blocksTableBody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 36px 20px; line-height: 1.6;">
            <div style="font-weight: 600; font-size: 15px; color: #e3b341; margin-bottom: 8px;">
              Notice: Unproxied requests are not detected
            </div>
            <div style="font-size: 12px; max-width: 600px; margin: 0 auto; color: var(--text-muted);">
              Automatic OS-level packet proxying without root/VPN is not supported.
              Sessions and turns appear dynamically as soon as an agent routes traffic through ctxins:

              <div class="empty-state-cmd-box">
                <div style="color: #8b949e; margin-bottom: 4px; font-size: 11px;"># Launch agent directly through ctxins proxy:</div>
                <div class="empty-state-cmd-row">
                  <code class="empty-state-cmd-text">ctxins run -- &lt;agent&gt;</code>
                  <button class="empty-state-copy-btn" data-copy="ctxins run -- <agent>" title="Copy command">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="copy-icon">
                      <path d="M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25v-7.5z"></path>
                      <path d="M5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25v-7.5zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25h-7.5z"></path>
                    </svg>
                    <span class="copy-text">Copy</span>
                    <span class="copy-feedback">Copied!</span>
                  </button>
                </div>

                <div style="color: #8b949e; margin-top: 10px; margin-bottom: 4px; font-size: 11px;"># Or export proxy environment in your agent terminal:</div>
                <div class="empty-state-cmd-row">
                  <code class="empty-state-cmd-text">eval $(ctxins env)</code>
                  <button class="empty-state-copy-btn" data-copy="eval $(ctxins env)" title="Copy command">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="copy-icon">
                      <path d="M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25v-7.5z"></path>
                      <path d="M5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25v-7.5zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25h-7.5z"></path>
                    </svg>
                    <span class="copy-text">Copy</span>
                    <span class="copy-feedback">Copied!</span>
                  </button>
                </div>

                <div style="color: #8b949e; margin-top: 10px; margin-bottom: 4px; font-size: 11px;"># To unset proxy environment variables when finished:</div>
                <div class="empty-state-cmd-row">
                  <code class="empty-state-cmd-text" style="color: #e3b341;">eval $(ctxins env --unset)</code>
                  <button class="empty-state-copy-btn" data-copy="eval $(ctxins env --unset)" title="Copy command">
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" class="copy-icon">
                      <path d="M0 6.75C0 5.784.784 5 1.75 5h1.5a.75.75 0 0 1 0 1.5h-1.5a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-1.5a.75.75 0 0 1 1.5 0v1.5A1.75 1.75 0 0 1 9.25 16h-7.5A1.75 1.75 0 0 1 0 14.25v-7.5z"></path>
                      <path d="M5 1.75C5 .784 5.784 0 6.75 0h7.5C15.216 0 16 .784 16 1.75v7.5A1.75 1.75 0 0 1 14.25 11h-7.5A1.75 1.75 0 0 1 5 9.25v-7.5zm1.75-.25a.25.25 0 0 0-.25.25v7.5c0 .138.112.25.25.25h7.5a.25.25 0 0 0 .25-.25v-7.5a.25.25 0 0 0-.25-.25h-7.5z"></path>
                    </svg>
                    <span class="copy-text">Copy</span>
                    <span class="copy-feedback">Copied!</span>
                  </button>
                </div>
              </div>

              <div style="margin-top: 20px;">
                <button id="load-demo-btn" class="btn btn-demo">
                  <svg width="15" height="15" viewBox="0 0 16 16" fill="currentColor">
                    <path d="M8 0a8 8 0 1 1 0 16A8 8 0 0 1 8 0zM6.5 5v6l5-3-5-3z"/>
                  </svg>
                  Explore Demo Session
                </button>
              </div>
            </div>
          </td>
        </tr>
      `;
      this._bindEmptyStateActions();
    }
  }

  _bindEmptyStateActions() {
    if (!this.blocksTableBody) return;
    const copyBtns = this.blocksTableBody.querySelectorAll('.empty-state-copy-btn');
    copyBtns.forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const text = btn.getAttribute('data-copy');
        if (!text) return;
        this._copyToClipboard(text, btn);
      });
    });

    const demoBtn = document.getElementById('load-demo-btn');
    if (demoBtn) {
      demoBtn.addEventListener('click', (e) => {
        e.preventDefault();
        this.loadDemoSession();
      });
    }
  }

  _copyToClipboard(text, btnElement) {
    const doFeedback = () => {
      if (!btnElement) return;
      btnElement.classList.add('copied');
      setTimeout(() => {
        btnElement.classList.remove('copied');
      }, 2000);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(doFeedback).catch(() => {
        this._fallbackCopy(text);
        doFeedback();
      });
    } else {
      this._fallbackCopy(text);
      doFeedback();
    }
  }

  _fallbackCopy(text) {
    const textArea = document.createElement('textarea');
    textArea.value = text;
    textArea.style.position = 'fixed';
    textArea.style.opacity = '0';
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    try {
      document.execCommand('copy');
    } catch (_) {}
    document.body.removeChild(textArea);
  }

  _populateDiffSelects() {
    if (!this.diffT1 || !this.diffT2) return;
    const currentT1 = this.diffT1.value;
    const currentT2 = this.diffT2.value;

    this.diffT1.innerHTML = '';
    this.diffT2.innerHTML = '';

    this.turns.forEach((t, i) => {
      const idx =
        t.turn_index !== undefined ? t.turn_index : t.turnIndex !== undefined ? t.turnIndex : i;
      const opt1 = document.createElement('option');
      opt1.value = idx;
      opt1.textContent = `Turn #${idx + 1}`;
      this.diffT1.appendChild(opt1);

      const opt2 = document.createElement('option');
      opt2.value = idx;
      opt2.textContent = `Turn #${idx + 1}`;
      this.diffT2.appendChild(opt2);
    });

    if (this.turns.length >= 2) {
      const prevTurn = this.turns[this.turns.length - 2];
      const lastTurn = this.turns[this.turns.length - 1];
      const prevIdx = prevTurn.turn_index ?? prevTurn.turnIndex ?? 0;
      const lastIdx = lastTurn.turn_index ?? lastTurn.turnIndex ?? 1;
      this.diffT1.value = currentT1 || prevIdx;
      this.diffT2.value = currentT2 || lastIdx;
    } else if (this.turns.length === 1) {
      const onlyTurn = this.turns[0];
      const onlyIdx = onlyTurn.turn_index ?? onlyTurn.turnIndex ?? 0;
      this.diffT1.value = currentT1 || onlyIdx;
      this.diffT2.value = currentT2 || onlyIdx;
    }
  }

  _populateTurnSelect() {
    if (!this.turnInspectorSelect) return;
    this.turnInspectorSelect.innerHTML = '';

    if (!this.turns || this.turns.length === 0) {
      const opt = document.createElement('option');
      opt.value = '';
      opt.textContent = 'No turns loaded';
      this.turnInspectorSelect.appendChild(opt);
      this._updateTurnNavControls();
      return;
    }

    this.turns.forEach((t, i) => {
      const idx =
        t.turn_index !== undefined ? t.turn_index : t.turnIndex !== undefined ? t.turnIndex : i;
      const inp = t.input_tokens ?? t.inputTokens ?? 0;
      const out = t.output_tokens ?? t.outputTokens ?? 0;
      const total = inp + out;
      const tokStr = total > 0 ? ` (${total.toLocaleString()} tok)` : '';
      const opt = document.createElement('option');
      opt.value = idx;
      opt.textContent = `Turn #${Number(idx) + 1}${tokStr}`;
      this.turnInspectorSelect.appendChild(opt);
    });

    this._updateTurnNavControls();
  }

  _updateTurnNavControls() {
    if (!this.turns || this.turns.length === 0) {
      if (this.turnInspectorSelect) this.turnInspectorSelect.value = '';
      if (this.turnPrevBtn) this.turnPrevBtn.disabled = true;
      if (this.turnNextBtn) this.turnNextBtn.disabled = true;
      if (this.turnLatestBtn) this.turnLatestBtn.style.display = 'none';
      return;
    }

    const currPos = this.turns.findIndex(
      (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === this.selectedTurnIndex
    );

    if (this.turnInspectorSelect && currPos >= 0) {
      this.turnInspectorSelect.value = String(this.selectedTurnIndex);
    }

    if (this.turnPrevBtn) {
      this.turnPrevBtn.disabled = currPos <= 0;
    }
    if (this.turnNextBtn) {
      this.turnNextBtn.disabled = currPos < 0 || currPos >= this.turns.length - 1;
    }
    if (this.turnLatestBtn) {
      const isLatest = currPos === this.turns.length - 1;
      this.turnLatestBtn.style.display = isLatest ? 'none' : 'inline-flex';
    }
  }

  navigateTurn(offset) {
    if (!this.turns || this.turns.length === 0) return;
    const currPos = this.turns.findIndex(
      (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === this.selectedTurnIndex
    );
    const startPos = currPos >= 0 ? currPos : this.turns.length - 1;
    const targetPos = startPos + offset;
    if (targetPos >= 0 && targetPos < this.turns.length) {
      const targetTurn = this.turns[targetPos];
      const targetIdx =
        targetTurn.turn_index !== undefined
          ? targetTurn.turn_index
          : targetTurn.turnIndex !== undefined
          ? targetTurn.turnIndex
          : targetPos;
      this.selectTurn(targetIdx, true);
    }
  }

  navigateLatestTurn() {
    if (!this.turns || this.turns.length === 0) return;
    const lastTurn = this.turns[this.turns.length - 1];
    const lastIdx =
      lastTurn.turn_index !== undefined
        ? lastTurn.turn_index
        : lastTurn.turnIndex !== undefined
        ? lastTurn.turnIndex
        : this.turns.length - 1;
    this._userPinnedHistoricalTurn = false;
    this.selectTurn(lastIdx, false);
  }

  getBlockInfo(blockId, preferredTurnIdx = null) {
    let foundBlock = null;
    let foundTurnIdx = null;

    if (preferredTurnIdx !== null && preferredTurnIdx !== undefined && !isNaN(Number(preferredTurnIdx))) {
      const pIdx = Number(preferredTurnIdx);
      const turn = this.turns.find((t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === pIdx);
      if (turn) {
        const blocks =
          turn.all_blocks ||
          turn.blocks || [
            ...(turn.system_blocks || turn.systemBlocks || []),
            ...(turn.tool_defs || turn.toolDefs || []),
            ...(turn.conversation_history || turn.conversationHistory || []),
            ...(turn.tool_results || turn.toolResults || []),
            ...(turn.assistant_blocks || turn.assistantBlocks || []),
          ];
        foundBlock = blocks.find((b) => (b.block_id || b.blockId) === blockId);
        if (foundBlock) foundTurnIdx = pIdx;
      }
    }

    if (!foundBlock) {
      for (const turn of this.turns) {
        const tIdx = turn.turn_index !== undefined ? turn.turn_index : turn.turnIndex;
        const blocks =
          turn.all_blocks ||
          turn.blocks || [
            ...(turn.system_blocks || turn.systemBlocks || []),
            ...(turn.tool_defs || turn.toolDefs || []),
            ...(turn.conversation_history || turn.conversationHistory || []),
            ...(turn.tool_results || turn.toolResults || []),
            ...(turn.assistant_blocks || turn.assistantBlocks || []),
          ];
        const b = blocks.find((blk) => (blk.block_id || blk.blockId) === blockId);
        if (b) {
          foundBlock = b;
          foundTurnIdx = tIdx;
          break;
        }
      }
    }

    return { block: foundBlock, turnIndex: foundTurnIdx };
  }

  locateAndHighlightBlock(blockId, preferredTurnIndex = null, openModal = false) {
    if (!blockId) return;

    // 1. Determine target turn
    const { block, turnIndex } = this.getBlockInfo(blockId, preferredTurnIndex);
    const targetTurn = turnIndex !== null && turnIndex !== undefined ? turnIndex : preferredTurnIndex;

    // 2. Switch turn if needed
    if (targetTurn !== null && targetTurn !== undefined) {
      if (this.selectedTurnIndex !== targetTurn) {
        this.selectTurn(targetTurn, true);
      }
    }

    const currentTurn = this.getSelectedTurn();

    // 3. Reset filter if active filter hides this block
    if (this.currentBlockFilter !== 'ALL') {
      let isVisible = false;
      if (block) {
        const f = this.currentBlockFilter;
        if (f === 'SYSTEM' && this._isSystemBlock(block)) isVisible = true;
        else if (f === 'SKILLS' && this._isSkillBlock(block)) isVisible = true;
        else if (f === 'TOOLS' && this._isToolDefBlock(block)) isVisible = true;
        else if (f === 'MESSAGES' && this._isMessageBlock(block)) isVisible = true;
        else if (f === 'TOOL_RESULTS' && (this._isToolResultBlock(block) || this._isToolCallBlock(block))) isVisible = true;
        else if (f === 'ADDED' && (block.lifecycle_status || block.status) === 'added') isVisible = true;
        else if (f === 'MUTATED' && (block.lifecycle_status || block.status) === 'mutated') isVisible = true;
      }
      if (!isVisible) {
        this.setBlockFilter('ALL');
      }
    }

    // 4. Check if section or exchange is collapsed
    let matchingRow = null;
    if (this.blocksTableBody) {
      const rows = Array.from(this.blocksTableBody.querySelectorAll('tr'));
      matchingRow = rows.find(
        (tr) =>
          tr.dataset.blockId === blockId ||
          tr.querySelector('.code-cell')?.textContent.includes(blockId)
      );
    }

    if (matchingRow) {
      const sec = matchingRow.dataset.section;
      const ex = matchingRow.dataset.exchange;
      let uncollapsedAny = false;
      if (sec && this.collapsedSections.has(sec)) {
        this.collapsedSections.delete(sec);
        uncollapsedAny = true;
      }
      if (ex && this.collapsedExchanges.has(ex)) {
        this.collapsedExchanges.delete(ex);
        uncollapsedAny = true;
      }
      if (this.collapsedSections.has('messages') && (sec === 'messages' || ex)) {
        this.collapsedSections.delete('messages');
        uncollapsedAny = true;
      }
      if (uncollapsedAny && currentTurn) {
        this.renderBlocksTable(currentTurn);
        const newRows = Array.from(this.blocksTableBody.querySelectorAll('tr'));
        matchingRow = newRows.find(
          (tr) =>
            tr.dataset.blockId === blockId ||
            tr.querySelector('.code-cell')?.textContent.includes(blockId)
        );
      }

      // Ensure matchingRow and all its containing section/exchange items are unhidden in DOM
      if (matchingRow) {
        matchingRow.classList.remove('exchange-hidden');
        if (sec) {
          this.collapsedSections.delete(sec);
          const secHeader = this.blocksTableBody?.querySelector(`.section-group-header[data-section="${sec}"]`);
          if (secHeader) secHeader.classList.remove('collapsed');
          const secItems = this.blocksTableBody?.querySelectorAll(`.exchange-item-row[data-section="${sec}"]`);
          secItems?.forEach((r) => r.classList.remove('exchange-hidden'));
        }
        if (ex) {
          this.collapsedExchanges.delete(ex);
          const exHeader = this.blocksTableBody?.querySelector(`.exchange-group-header[data-exchange="${ex}"]`);
          if (exHeader) exHeader.classList.remove('collapsed');
          const exItems = this.blocksTableBody?.querySelectorAll(`.exchange-item-row[data-exchange="${ex}"]`);
          exItems?.forEach((r) => r.classList.remove('exchange-hidden'));
        }
      }
    }

    // 5. Scroll and highlight
    if (matchingRow) {
      matchingRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
      matchingRow.classList.remove('highlight-diff-target');
      matchingRow.classList.remove('highlight-culprit-row');
      void matchingRow.offsetWidth; // Force CSS reflow to restart animation
      matchingRow.classList.add('highlight-diff-target');
      matchingRow.classList.add('highlight-culprit-row');
      setTimeout(() => {
        matchingRow.classList.remove('highlight-diff-target');
        matchingRow.classList.remove('highlight-culprit-row');
      }, 3500);

      const turnLabel = targetTurn !== null && targetTurn !== undefined ? `Turn #${Number(targetTurn) + 1}` : 'current turn';
      this.showToast(`Traced block ${blockId} to ${turnLabel} context panel`);
    } else {
      document.getElementById('blocks-table-body')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const turnLabel = targetTurn !== null && targetTurn !== undefined ? `Turn #${Number(targetTurn) + 1}` : 'current turn';
      this.showToast(`Block ${blockId} in ${turnLabel}`);
    }

    // 6. Open modal if requested
    if (openModal) {
      const modalBlock = block || { block_id: blockId };
      const roleLabel = block ? (block.block_type || block.blockType || 'Block') : 'Block';
      this.openModal(
        `Inspect Block: ${blockId} (${roleLabel})`,
        modalBlock.content || JSON.stringify(modalBlock, null, 2)
      );
    }
  }

  async computeDiff() {
    if (!this.activeSessionId || !this.diffT1 || !this.diffT2 || !this.diffResults) return;
    const t1 = this.diffT1.value;
    const t2 = this.diffT2.value;

    if (t1 === '' || t2 === '') return;

    if (this.activeSessionId && this.activeSessionId.startsWith('demo-')) {
      const turn1 = this.turns.find((t) => (t.turn_index ?? t.turnIndex ?? 0) == t1);
      const turn2 = this.turns.find((t) => (t.turn_index ?? t.turnIndex ?? 0) == t2);
      if (turn1 && turn2) {
        const b1 = (turn1.all_blocks || turn1.blocks || []).map((b) => b.block_id || b.blockId);
        const b2 = (turn2.all_blocks || turn2.blocks || []).map((b) => b.block_id || b.blockId);
        const b1Set = new Set(b1);
        const b2Set = new Set(b2);

        const added = b2.filter((id) => !b1Set.has(id));
        const removed = b1.filter((id) => !b2Set.has(id));
        const persisted = b2.filter((id) => b1Set.has(id));
        const mutated = (turn2.all_blocks || turn2.blocks || [])
          .filter((b) => (b.lifecycle_status || b.status) === 'mutated')
          .map((b) => b.block_id || b.blockId);
        const tokenGrowth = (turn2.input_tokens ?? turn2.inputTokens ?? 0) - (turn1.input_tokens ?? turn1.inputTokens ?? 0);

        this.renderDiffResults({
          tokenGrowth,
          addedBlockIds: added,
          mutatedBlockIds: mutated,
          removedBlockIds: removed,
          persistedBlockIds: persisted,
          cacheBreakpointBlockId: added.length > 0 ? added[0] : null,
        }, t1, t2);
        return;
      }
    }

    try {
      const res = await fetch(`/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/diff/${t1}/${t2}`);
      if (res.ok) {
        const data = await res.json();
        this.renderDiffResults(data, t1, t2);
      } else {
        const err = await res.json();
        this.diffResults.innerHTML = `<div style="color: var(--color-critical); padding: 8px;">Error: ${err.detail || 'Diff failed'}</div>`;
      }
    } catch (err) {
      console.error('[DashboardApp] Diff calculation failed:', err);
    }
  }

  renderDiffResults(data, fromTurnOverride = null, toTurnOverride = null) {
    if (!this.diffResults) return;
    const growth = data.tokenGrowth ?? data.token_growth ?? 0;
    const growthColor = growth > 0 ? 'var(--color-critical)' : 'var(--color-success)';
    const growthPrefix = growth > 0 ? '+' : '';

    const added = data.addedBlockIds || data.added_block_ids || [];
    const mutated = data.mutatedBlockIds || data.mutated_block_ids || [];
    const removed = data.removedBlockIds || data.removed_block_ids || [];
    const persisted = data.persistedBlockIds || data.persisted_block_ids || [];
    const breakpoint = data.cacheBreakpointBlockId || data.cache_breakpoint_block_id || null;

    const t1Val = fromTurnOverride !== null && fromTurnOverride !== undefined
      ? parseInt(fromTurnOverride, 10)
      : (data.fromTurnIndex !== undefined ? data.fromTurnIndex : (this.diffT1 ? parseInt(this.diffT1.value, 10) : null));
    const t2Val = toTurnOverride !== null && toTurnOverride !== undefined
      ? parseInt(toTurnOverride, 10)
      : (data.toTurnIndex !== undefined ? data.toTurnIndex : (this.diffT2 ? parseInt(this.diffT2.value, 10) : null));

    const renderBlockPills = (arr, badgeClass, targetTurn, actionDesc) => {
      if (arr.length === 0) return '<span style="color: var(--text-secondary); font-size: 11px;">None</span>';
      return arr.map((id) => {
        const { block, turnIndex } = this.getBlockInfo(id, targetTurn);
        const effectiveTurn = turnIndex !== null && turnIndex !== undefined ? turnIndex : targetTurn;
        const effectiveTurnNum = effectiveTurn !== null && effectiveTurn !== undefined ? Number(effectiveTurn) + 1 : '?';
        let previewText = '';
        if (block) {
          const snippet = this._extractBlockSnippet(block, 60);
          const tok = block.token_count ?? block.tokenCount;
          const tokStr = tok !== undefined ? ` • ${tok.toLocaleString()} tok` : '';
          previewText = snippet ? ` — "${snippet}"${tokStr}` : tokStr;
        }
        const titleText = `[Turn #${effectiveTurnNum}] ${actionDesc} block "${id}"${previewText} — Click to trace in panel above`;

        return `
          <div class="diff-block-pill badge ${badgeClass}" data-block-id="${this._escapeHtml(id)}" data-turn="${effectiveTurn}" role="button" tabindex="0" title="${this._escapeHtml(titleText)}">
            <span class="diff-block-link-text">${this._escapeHtml(id)}</span>
            <button type="button" class="diff-block-inspect-btn" data-block-id="${this._escapeHtml(id)}" data-turn="${effectiveTurn}" title="Inspect full content for '${this._escapeHtml(id)}'">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                <path d="M8 2a7.5 7.5 0 0 0-7.46 6.88 1 1 0 0 0 .92 1.12.94.94 0 0 0 1.04-.84A5.5 5.5 0 1 1 8 13.5a5.45 5.45 0 0 1-3.66-1.42 1 1 0 1 0-1.34 1.48A7.5 7.5 0 1 0 8 2zm0 3.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z"/>
              </svg>
            </button>
          </div>
        `;
      }).join(' ');
    };

    let breakpointHtml = '';
    if (breakpoint) {
      const { block: bpBlock, turnIndex: bpTurn } = this.getBlockInfo(breakpoint, t2Val);
      const effectiveBpTurn = bpTurn !== null && bpTurn !== undefined ? bpTurn : t2Val;
      const effectiveBpTurnNum = effectiveBpTurn !== null && effectiveBpTurn !== undefined ? Number(effectiveBpTurn) + 1 : '?';
      let bpPreview = '';
      if (bpBlock) {
        const snippet = this._extractBlockSnippet(bpBlock, 60);
        const tok = bpBlock.token_count ?? bpBlock.tokenCount;
        const tokStr = tok !== undefined ? ` • ${tok.toLocaleString()} tok` : '';
        bpPreview = snippet ? ` — "${snippet}"${tokStr}` : tokStr;
      }
      const bpTitle = `[Turn #${effectiveBpTurnNum}] Prefix cache breakpoint at "${breakpoint}"${bpPreview} — Click to trace in panel above`;

      breakpointHtml = `
        <div class="diff-card">
          <div class="diff-card-title">Prefix Cache Breakpoint</div>
          <div class="diff-badge-list">
            <div class="diff-block-pill badge badge-breakpoint" data-block-id="${this._escapeHtml(breakpoint)}" data-turn="${effectiveBpTurn}" role="button" tabindex="0" title="${this._escapeHtml(bpTitle)}">
              <span class="diff-block-link-text">⚡ ${this._escapeHtml(breakpoint)}</span>
              <button type="button" class="diff-block-inspect-btn" data-block-id="${this._escapeHtml(breakpoint)}" data-turn="${effectiveBpTurn}" title="Inspect full content for breakpoint '${this._escapeHtml(breakpoint)}'">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                  <path d="M8 2a7.5 7.5 0 0 0-7.46 6.88 1 1 0 0 0 .92 1.12.94.94 0 0 0 1.04-.84A5.5 5.5 0 1 1 8 13.5a5.45 5.45 0 0 1-3.66-1.42 1 1 0 1 0-1.34 1.48A7.5 7.5 0 1 0 8 2zm0 3.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      `;
    }

    this.diffResults.innerHTML = `
      <div class="diff-results-hint">
        <span>🔗 Traceability: Click any block badge to jump to and highlight its content in the context panel above, or click 👁 to open full content modal.</span>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Token Growth</div>
        <div class="diff-card-value" style="color: ${growthColor};">${growthPrefix}${growth.toLocaleString()} tok</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Added Blocks (${added.length})</div>
        <div class="diff-badge-list">${renderBlockPills(added, 'badge-added', t2Val, 'Added')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Mutated Blocks (${mutated.length})</div>
        <div class="diff-badge-list">${renderBlockPills(mutated, 'badge-mutated', t2Val, 'Mutated')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Evicted Blocks (${removed.length})</div>
        <div class="diff-badge-list">${renderBlockPills(removed, 'badge-evicted', t1Val, 'Evicted')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Persisted Blocks (${persisted.length})</div>
        <div class="diff-badge-list">${renderBlockPills(persisted, 'badge-persisted', t2Val, 'Persisted')}</div>
      </div>
      ${breakpointHtml}
    `;
  }

  exportSession() {
    if (!this.activeSessionId) {
      alert('No active session to export.');
      return;
    }
    if (this.activeSessionId && this.activeSessionId.startsWith('demo-')) {
      const exportData = {
        sessionId: this.activeSessionId,
        summary: this.summary,
        turns: this.turns,
        violations: this.violations,
        exported_at: new Date().toISOString(),
      };
      const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${this.activeSessionId}-export.jsonc`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return;
    }
    const exportUrl = `/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/export?format=jsonc`;
    window.location.href = exportUrl;
  }

  exportMarkdownReport() {
    if (!this.activeSessionId) {
      alert('No active session to export.');
      return;
    }
    if (this.activeSessionId === 'demo-claude-session') {
      const report = this.generateMarkdownAudit();
      const blob = new Blob([report], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${this.activeSessionId}_optimization_report.md`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return;
    }
    const exportUrl = `/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/export?format=markdown`;
    window.location.href = exportUrl;
  }

  generateMarkdownAudit() {
    const s = this.summary || {};
    const totalInput = s.totalInputTokens ?? 0;
    const totalOutput = s.totalOutputTokens ?? 0;
    const totalTokens = totalInput + totalOutput;
    const hitRatio = Math.round((s.cacheHitRatio ?? 0) * 1000) / 10;
    const spend = Number(s.estimatedCostUSD ?? 0).toFixed(4);
    const waste = Number(s.potentialSavingsUSD ?? 0).toFixed(4);
    const score = Number(s.pollutionScore ?? 0).toFixed(1);

    let doc = `# 🔍 ctxins Context Optimization Report: \`${this.activeSessionId}\`\n\n`;
    doc += `- **Generated:** ${new Date().toISOString()}\n`;
    doc += `- **Agent Harness:** Claude Code (Demo)\n`;
    doc += `- **Total Turns:** ${this.turns.length}\n\n`;
    doc += `---\n\n## 📊 Executive Summary & Financial Audit\n\n`;
    doc += `| Metric | Value | Assessment |\n| :--- | :--- | :--- |\n`;
    doc += `| **Total Tokens** | ${totalTokens.toLocaleString()} (${totalInput.toLocaleString()} in / ${totalOutput.toLocaleString()} out) | Combined cumulative context |\n`;
    doc += `| **Prompt Cache Hit %** | ${hitRatio}% | Cached read ratio |\n`;
    doc += `| **Estimated Total Spend** | $${spend} USD | Total model API cost |\n`;
    doc += `| **Avoidable Waste** | **$${waste} USD** | Recoverable financial waste |\n`;
    doc += `| **Context Pollution Score** | **${score} / 100** | Diagnostic score |\n\n`;
    doc += `---\n\n## 🚨 Triggered Context Health Violations\n\n`;

    if (this.violations.length === 0) {
      doc += `> ✨ **Zero Context Violations Detected**\n\n`;
    } else {
      this.violations.forEach((v, i) => {
        const title = v.title || v.rule_id || 'Alert';
        const sev = v.severity || 'INFO';
        const msg = v.message || '';
        const fix = v.suggested_fix || '';
        const wasteVal = v.estimated_waste_usd ? ` ($${Number(v.estimated_waste_usd).toFixed(4)} waste)` : '';
        doc += `### ${i + 1}. [${sev}] ${title}${wasteVal}\n- **Rule ID:** \`${v.rule_id || 'RULE'}\`\n- **Diagnostic:** ${msg}\n- **Remediation:** ${fix}\n\n`;
      });
    }

    doc += `---\n\n## 🛠️ Recommended Directives for AGENTS.md / .cursorrules\n\n`;
    doc += `\`\`\`markdown\n# Context Optimization Directives (ctxins)\n`;
    this.violations.forEach((v) => {
      doc += `${this.generateDirective(v)}\n\n`;
    });
    doc += `\`\`\`\n\n---\n*Report generated by [ctxins](https://github.com/arnabkaycee/ctxins)*\n`;
    return doc;
  }

  openModal(title, content) {
    if (this.modalTitle) this.modalTitle.textContent = title;
    const textRepresentation =
      typeof content === 'object' && content !== null
        ? JSON.stringify(content, null, 2)
        : String(content || '');
    if (this.modalBody) this.modalBody.textContent = textRepresentation;

    if (this.jsonViewer) {
      this.jsonViewer.render(content);
    }
    if (this.modalOverlay) this.modalOverlay.classList.add('active');
  }

  closeModal() {
    if (this.modalOverlay) this.modalOverlay.classList.remove('active');
  }

  generateDirective(v) {
    const ruleId = (v.rule_id || v.ruleId || '').toUpperCase();
    if (ruleId.includes('CTX001')) {
      return `# Context Directive: Compact Stale Tool Results
- Summarize tool outputs older than 3 turns into key findings; omit raw stdout/stderr.`;
    } else if (ruleId.includes('CTX002')) {
      return `# Context Directive: Prune Unused Tool Schemas
- Do not include tool schemas in system prompt until invoked or explicitly required.`;
    } else if (ruleId.includes('CACHE001')) {
      return `# Context Directive: Cache Stability
- Keep system prompts and tool declarations deterministic and static at the start of context.`;
    } else if (ruleId.includes('CTX004') || ruleId.includes('CTX-004') || ruleId.includes('RECURRING')) {
      return `# Context Directive: Shrink Context & Eliminate Recurring Results
- Compact repetitive tool execution results older than 2 turns (/compact).
- When recurring results exceed context threshold, start a fresh session (/clear) preserving only milestone summary.
- Transmit incremental diffs rather than repetitive raw dumps.`;
    } else {
      const title = v.title || v.rule_id || v.ruleId || 'Context Directive';
      const fix = v.suggested_fix || v.suggestedFix || v.message || 'Optimize context efficiency.';
      return `# Context Directive: ${title}
- ${fix}`;
    }
  }

  showToast(message, duration = 3000) {
    let toastContainer = document.getElementById('ctxins-toast-container');
    if (!toastContainer) {
      toastContainer = document.createElement('div');
      toastContainer.id = 'ctxins-toast-container';
      toastContainer.className = 'ctxins-toast-container';
      document.body.appendChild(toastContainer);
    }
    const toast = document.createElement('div');
    toast.className = 'ctxins-toast';
    toast.textContent = message;
    toastContainer.appendChild(toast);
    setTimeout(() => {
      toast.classList.add('fade-out');
      setTimeout(() => toast.remove(), 300);
    }, duration);
  }

  fallbackCopyText(text) {
    try {
      const textarea = document.createElement('textarea');
      textarea.value = text;
      textarea.style.position = 'fixed';
      textarea.style.top = '-9999px';
      textarea.style.left = '-9999px';
      textarea.setAttribute('readonly', '');
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    } catch (err) {
      console.warn('[DashboardApp] Fallback copy failed:', err);
    }
  }

  loadDemoSession() {
    const demoData = this._getDemoSessionData();
    const demoSession = {
      sessionId: 'demo-claude-session',
      agentHarness: 'Claude Code',
      model: 'claude-3-7-sonnet',
      turnCount: 4,
      createdAt: new Date().toISOString(),
    };

    if (!this.sessions.some((s) => s.sessionId === demoSession.sessionId)) {
      this.sessions.unshift(demoSession);
    }
    this.activeSessionId = demoSession.sessionId;
    this._populateSessionSelect();

    this.turns = demoData.turns;
    this.violations = demoData.violations;
    this.summary = demoData.summary;

    if (this.statusPill) {
      this.statusPill.className = 'status-pill connected';
    }
    if (this.statusText) {
      this.statusText.textContent = 'Demo Sandbox (claude-3-7-sonnet)';
    }

    this.renderAll();
  }

  _getDemoSessionData() {
    const generateTestOutput = () => {
      const lines = [
        '============================= test session starts =============================',
        'platform darwin -- Python 3.11.8, pytest-8.1.1, pluggy-1.4.0',
        'rootdir: /workspace/ctxins',
        'configfile: pyproject.toml',
        'collected 250 items',
        '',
      ];
      for (let i = 1; i <= 242; i++) {
        const padded = String(i).padStart(3, '0');
        const pct = Math.floor((i / 250) * 100);
        lines.push(`tests/unit/test_module_${padded}.py::test_worker_spec_${padded} PASSED [${pct}%]`);
      }
      lines.push('tests/unit/test_auth.py::test_token_generation PASSED [97%]');
      lines.push('tests/unit/test_auth.py::test_token_refresh PASSED [98%]');
      lines.push('tests/unit/test_auth.py::test_jwt_token_expiry FAILED [99%]');
      lines.push('tests/unit/test_auth.py::test_user_permissions PASSED [100%]');
      lines.push('');
      lines.push('=================================== FAILURES ===================================');
      lines.push('_____________________________ test_jwt_token_expiry _____________________________');
      lines.push('def test_jwt_token_expiry():');
      lines.push('        auth_svc = AuthService(secret="test_secret_k8s")');
      lines.push('        token = auth_svc.issue_jwt(sub="usr_481", ttl_seconds=300)');
      lines.push('>       assert auth_svc.validate_jwt(token, current_time=now + 301) is False');
      lines.push('E       AssertionError: assert True is False');
      lines.push('E       + where True = validate_jwt("eyJhbGciOi...", current_time=1710931501)');
      lines.push('');
      lines.push('tests/unit/test_auth.py:84: AssertionError');
      lines.push('=========================== short test summary info ============================');
      lines.push('FAILED tests/unit/test_auth.py::test_jwt_token_expiry - AssertionError: assert True is False');
      lines.push('======================= 1 failed, 249 passed in 4.12s ==========================');
      return lines.join('\n');
    };

    const turns = [
      {
        turn_index: 0,
        input_tokens: 4500,
        output_tokens: 350,
        cached_read_tokens: 0,
        turn_cost_usd: 0.0135,
        duration_ms: 1850,
        ttft_ms: 320,
        category_breakdown: {
          system: 1200,
          tools: 2200,
          skills: 300,
          history: 650,
          tool_results: 0,
          thoughts: 150,
        },
        all_blocks: [
          {
            block_id: 'blk-sys-instructions',
            block_type: 'system',
            identity_key: 'system:core_instructions',
            token_count: 1200,
            turns_survived: 3,
            content_hash: '3f7a1b9c',
            lifecycle_status: 'added',
            content: {
              role: 'system',
              instructions: 'You are Claude Code, an expert agentic software engineer.\nOperate carefully in user workspaces. Read code before editing. Run tests to verify all changes.\nAvoid context bloat and stale result survival.',
            },
          },
          {
            block_id: 'blk-tool-schemas',
            block_type: 'tool_defs',
            identity_key: 'tools:all_definitions',
            token_count: 2200,
            turns_survived: 3,
            content_hash: '9a4d8c2e',
            lifecycle_status: 'added',
            content: [
              {
                name: 'execute_bash',
                description: 'Run commands in isolated bash sandbox environment',
                parameters: { type: 'object', properties: { command: { type: 'string' } }, required: ['command'] },
              },
              {
                name: 'edit_file',
                description: 'Perform precise search-and-replace text modifications to workspace files',
                parameters: { type: 'object', properties: { target_file: { type: 'string' }, old_content: { type: 'string' }, new_content: { type: 'string' } }, required: ['target_file', 'old_content', 'new_content'] },
              },
              {
                name: 'database_query',
                description: 'Execute analytical SQL queries against telemetry data warehouse',
                parameters: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] },
              },
              {
                name: 'web_search',
                description: 'Perform targeted technical documentation searches',
                parameters: { type: 'object', properties: { q: { type: 'string' } }, required: ['q'] },
              },
            ],
          },
          {
            block_id: 'blk-skill-pytest',
            block_type: 'skill',
            identity_key: 'skill:testing_standard',
            token_count: 300,
            turns_survived: 3,
            content_hash: '5d8e2a1b',
            lifecycle_status: 'added',
            content: '# Skill: Pytest Testing Standards\nAlways run pytest with concise output and focus on the first failure traceback.',
          },
          {
            block_id: 'blk-user-prompt-0',
            block_type: 'conversation_history',
            identity_key: 'user:turn_0_prompt',
            token_count: 650,
            turns_survived: 3,
            content_hash: '7b1c3d5e',
            lifecycle_status: 'added',
            content: {
              role: 'user',
              content: 'Fix the intermittent authentication test failure in test_auth.py and make sure all tests pass cleanly.',
            },
          },
          {
            block_id: 'blk-tht-turn-0',
            block_type: 'thought',
            identity_key: 'assistant:plan_init',
            token_count: 150,
            turns_survived: 0,
            content_hash: '1c2b4a6f',
            lifecycle_status: 'added',
            content: 'I will first execute the test suite via execute_bash to locate the failing assertion in test_auth.py.',
          },
          {
            block_id: 'blk-call-bash-0',
            block_type: 'assistant',
            identity_key: 'assistant:tool_call:execute_bash',
            token_count: 200,
            turns_survived: 0,
            content_hash: '2d4e6f8a',
            lifecycle_status: 'added',
            content: {
              action: 'call',
              tool: 'execute_bash',
              arguments: { command: 'pytest tests/ -v' },
            },
          },
        ],
      },
      {
        turn_index: 1,
        input_tokens: 6200,
        output_tokens: 420,
        cached_read_tokens: 3800,
        turn_cost_usd: 0.0098,
        duration_ms: 2450,
        ttft_ms: 280,
        category_breakdown: {
          system: 1200,
          tools: 2200,
          skills: 300,
          history: 920,
          tool_results: 1400,
          thoughts: 180,
        },
        all_blocks: [
          {
            block_id: 'blk-sys-instructions',
            block_type: 'system',
            identity_key: 'system:core_instructions',
            token_count: 1200,
            turns_survived: 2,
            content_hash: '3f7a1b9c',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-tool-schemas',
            block_type: 'tool_defs',
            identity_key: 'tools:all_definitions',
            token_count: 2200,
            turns_survived: 2,
            content_hash: '9a4d8c2e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-skill-pytest',
            block_type: 'skill',
            identity_key: 'skill:testing_standard',
            token_count: 300,
            turns_survived: 2,
            content_hash: '5d8e2a1b',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-user-prompt-0',
            block_type: 'conversation_history',
            identity_key: 'user:turn_0_prompt',
            token_count: 650,
            turns_survived: 2,
            content_hash: '7b1c3d5e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-hist-asst-0',
            block_type: 'conversation_history',
            identity_key: 'assistant:turn_0_response',
            token_count: 270,
            turns_survived: 2,
            content_hash: '4a5b6c7d',
            lifecycle_status: 'added',
            content: 'Running pytest to inspect all test results across 250 unit test cases...',
          },
          {
            block_id: 'blk-result-pytest-250',
            block_type: 'tool_results',
            identity_key: 'tool_result:pytest_250_lines',
            token_count: 1400,
            turns_survived: 1,
            content_hash: '8f9e0a1b',
            lifecycle_status: 'added',
            content: generateTestOutput(),
          },
          {
            block_id: 'blk-tht-turn-1',
            block_type: 'thought',
            identity_key: 'assistant:diagnose_jwt_failure',
            token_count: 180,
            turns_survived: 0,
            content_hash: '6e7f8a9b',
            lifecycle_status: 'added',
            content: 'The failure occurred in test_jwt_token_expiry because token leeway is 0s while clock skew is 1s. Let me edit src/auth/service.py to allow 5s grace leeway.',
          },
          {
            block_id: 'blk-call-edit-1',
            block_type: 'assistant',
            identity_key: 'assistant:tool_call:edit_file',
            token_count: 240,
            turns_survived: 0,
            content_hash: '5c6d7e8f',
            lifecycle_status: 'added',
            content: {
              action: 'call',
              tool: 'edit_file',
              arguments: {
                target_file: 'src/auth/service.py',
                old_content: 'leeway_seconds = 0',
                new_content: 'leeway_seconds = 5',
              },
            },
          },
        ],
      },
      {
        turn_index: 2,
        input_tokens: 7800,
        output_tokens: 510,
        cached_read_tokens: 5600,
        turn_cost_usd: 0.0142,
        duration_ms: 2950,
        ttft_ms: 310,
        category_breakdown: {
          system: 1200,
          tools: 2200,
          skills: 300,
          history: 2480,
          tool_results: 1400,
          thoughts: 220,
        },
        all_blocks: [
          {
            block_id: 'blk-sys-instructions',
            block_type: 'system',
            identity_key: 'system:core_instructions',
            token_count: 1200,
            turns_survived: 1,
            content_hash: '3f7a1b9c',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-tool-schemas',
            block_type: 'tool_defs',
            identity_key: 'tools:all_definitions',
            token_count: 2200,
            turns_survived: 1,
            content_hash: '9a4d8c2e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-skill-pytest',
            block_type: 'skill',
            identity_key: 'skill:testing_standard',
            token_count: 300,
            turns_survived: 1,
            content_hash: '5d8e2a1b',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-user-prompt-0',
            block_type: 'conversation_history',
            identity_key: 'user:turn_0_prompt',
            token_count: 650,
            turns_survived: 1,
            content_hash: '7b1c3d5e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-result-pytest-250',
            block_type: 'tool_results',
            identity_key: 'tool_result:pytest_250_lines',
            token_count: 1400,
            turns_survived: 1,
            content_hash: '8f9e0a1b',
            lifecycle_status: 'persisted',
            content: generateTestOutput(),
          },
          {
            block_id: 'blk-result-edit-file',
            block_type: 'tool_results',
            identity_key: 'tool_result:edit_file_ack',
            token_count: 120,
            turns_survived: 1,
            content_hash: '3b4c5d6e',
            lifecycle_status: 'added',
            content: { success: true, target_file: 'src/auth/service.py', patch_applied: true },
          },
          {
            block_id: 'blk-result-git-status',
            block_type: 'tool_results',
            identity_key: 'tool_result:git_status',
            token_count: 180,
            turns_survived: 1,
            content_hash: '2a3b4c5d',
            lifecycle_status: 'added',
            content: 'On branch main\nChanges not staged for commit:\n\tmodified:   src/auth/service.py\n\nno changes added to commit (use "git add" to track)',
          },
          {
            block_id: 'blk-tht-turn-2',
            block_type: 'thought',
            identity_key: 'assistant:check_retest',
            token_count: 220,
            turns_survived: 0,
            content_hash: '9e0a1b2c',
            lifecycle_status: 'added',
            content: 'The patch was cleanly applied to src/auth/service.py. Now executing git status and preparing to run the verification test.',
          },
          {
            block_id: 'blk-call-bash-2',
            block_type: 'assistant',
            identity_key: 'assistant:tool_call:retest',
            token_count: 210,
            turns_survived: 0,
            content_hash: '8d9e0a1b',
            lifecycle_status: 'added',
            content: {
              action: 'call',
              tool: 'execute_bash',
              arguments: { command: 'pytest tests/unit/test_auth.py::test_jwt_token_expiry' },
            },
          },
        ],
      },
      {
        turn_index: 3,
        input_tokens: 8000,
        output_tokens: 720,
        cached_read_tokens: 6800,
        turn_cost_usd: 0.0110,
        duration_ms: 1650,
        ttft_ms: 260,
        category_breakdown: {
          system: 1200,
          tools: 2200,
          skills: 300,
          history: 4180,
          tool_results: 0,
          thoughts: 120,
        },
        all_blocks: [
          {
            block_id: 'blk-sys-instructions',
            block_type: 'system',
            identity_key: 'system:core_instructions',
            token_count: 1200,
            turns_survived: 0,
            content_hash: '3f7a1b9c',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-tool-schemas',
            block_type: 'tool_defs',
            identity_key: 'tools:all_definitions',
            token_count: 2200,
            turns_survived: 0,
            content_hash: '9a4d8c2e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-skill-pytest',
            block_type: 'skill',
            identity_key: 'skill:testing_standard',
            token_count: 300,
            turns_survived: 0,
            content_hash: '5d8e2a1b',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-user-prompt-0',
            block_type: 'conversation_history',
            identity_key: 'user:turn_0_prompt',
            token_count: 650,
            turns_survived: 0,
            content_hash: '7b1c3d5e',
            lifecycle_status: 'persisted',
          },
          {
            block_id: 'blk-history-consolidated',
            block_type: 'conversation_history',
            identity_key: 'history:turns_1_2_context',
            token_count: 3530,
            turns_survived: 0,
            content_hash: '4d5e6f7a',
            lifecycle_status: 'added',
            content: 'History: Identified test failure in test_jwt_token_expiry, modified src/auth/service.py leeway to 5s, verified git working directory status.',
          },
          {
            block_id: 'blk-tht-turn-3',
            block_type: 'thought',
            identity_key: 'assistant:final_wrapup',
            token_count: 120,
            turns_survived: 0,
            content_hash: '1a2b3c4d',
            lifecycle_status: 'added',
            content: 'Verification test passed with 1 passed in 0.08s. Task completed successfully.',
          },
          {
            block_id: 'blk-asst-success',
            block_type: 'assistant',
            identity_key: 'assistant:final_message',
            token_count: 720,
            turns_survived: 0,
            content_hash: '9b8a7c6d',
            lifecycle_status: 'added',
            content: 'I have investigated the flaky authentication test and identified that `test_jwt_token_expiry` was failing due to 0-second clock skew tolerance. By updating `leeway_seconds = 5` in `src/auth/service.py`, token expiry validation now accounts for realistic cluster clock jitter. All test suites pass successfully!',
          },
        ],
      },
    ];

    const violations = [
      {
        rule_id: 'CTX001',
        ruleId: 'CTX001',
        title: 'Stale Tool Output in Turn 2',
        severity: 'WARN',
        message: 'Tool result from Turn 1 (250 lines of pytest output, 1,400 tokens) survived into Turn 2 unreferenced, creating context drag.',
        suggested_fix: 'Truncate tool outputs or evict unreferenced execution results after subsequent tool invocations to recover context bandwidth.',
        estimated_waste_usd: 0.0042,
        estimatedWasteUSD: 0.0042,
        affected_turns: [2],
      },
      {
        rule_id: 'CTX002',
        ruleId: 'CTX002',
        title: 'Unused Tool Schema Bloat',
        severity: 'WARN',
        message: '3 tool definitions (database_query, web_search, deploy_preview totaling 1,650 tokens) were declared in system prompt but never invoked across the session.',
        suggested_fix: 'Use dynamic tool provisioning or defer unneeded tool schemas to specialized subagents to conserve prompt cache tokens.',
        estimated_waste_usd: 0.0085,
        estimatedWasteUSD: 0.0085,
        affected_turns: [0, 1, 2, 3],
      },
      {
        rule_id: 'CTX-004',
        ruleId: 'CTX-004',
        title: 'Recurring Execution Results Exceeded',
        severity: 'WARN',
        message: 'Tool result (pytest 250 test lines, 1,400 tokens) recurred across Turns 2 & 3 without compression, consuming redundant context bandwidth.',
        suggested_fix: 'Recurring results exceed threshold (1,400 tokens). Optimization: 1) Shrink context by compacting repetitive tool outputs (/compact); 2) Start a fresh session (/clear) preserving only milestone summary; 3) Use targeted flags (e.g. pytest -q --tb=short).',
        estimated_waste_usd: 0.0070,
        estimatedWasteUSD: 0.0070,
        affected_turns: [1, 2],
        block_ids: ['blk-result-pytest-250'],
      },
    ];

    const summary = {
      totalInputTokens: 26500,
      totalOutputTokens: 2000,
      totalTokens: 28500,
      cacheHitRatio: 0.685,
      estimatedCostUSD: 0.0485,
      potentialSavingsUSD: 0.0127,
      pollutionScore: 34.2,
      contextCapacityTokens: 200000,
      contextCapacityTokensK: 200.0,
      latestContextTokens: 26500,
      latestContextTokensK: 26.5,
      contextUsageRatio: 0.1325,
      contextUsagePercent: 13.3,
    };

    return { turns, violations, summary };
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const app = new DashboardApp();
  app.init();
  window.dashboardApp = app;
});
