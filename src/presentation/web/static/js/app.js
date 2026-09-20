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
    this.exportBtn = document.getElementById('export-btn');

    // KPI Elements
    this.kpiTokens = document.getElementById('kpi-tokens');
    this.kpiCacheHit = document.getElementById('kpi-cache-hit');
    this.kpiSpend = document.getElementById('kpi-spend');
    this.kpiAvoidable = document.getElementById('kpi-avoidable');
    this.kpiPollutionScore = document.getElementById('kpi-pollution-score');
    this.pollutionMeterFill = document.getElementById('pollution-meter-fill');
    this.pollutionLevelText = document.getElementById('pollution-level-text');

    // Feeds & Tables
    this.recommendationsFeed = document.getElementById('recommendations-feed');
    this.recommendationsCount = document.getElementById('recommendations-count');
    this.turnTitle = document.getElementById('selected-turn-title');
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
    this.charts = new DashboardCharts('token-chart', (turnIndex) => {
      this.selectTurn(turnIndex);
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

    if (this.exportBtn) {
      this.exportBtn.addEventListener('click', () => this.exportSession());
    }

    if (this.diffBtn) {
      this.diffBtn.addEventListener('click', () => this.computeDiff());
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

    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.modalOverlay && this.modalOverlay.classList.contains('active')) {
        this.closeModal();
      }
    });
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
      this.statusText.textContent = 'Live Connected';
    } else if (status === 'reconnecting') {
      this.statusText.textContent = 'Reconnecting...';
    } else {
      this.statusText.textContent = 'Disconnected';
    }
  }

  renderAll() {
    this.renderKPIs();
    if (this.charts) {
      this.charts.updateData(this.turns);
    }
    this.renderRecommendations();
    this._populateDiffSelects();

    // Select latest turn if none or selected out of bounds
    if (this.turns.length > 0) {
      const lastTurn = this.turns[this.turns.length - 1];
      const defaultIdx =
        lastTurn.turn_index !== undefined
          ? lastTurn.turn_index
          : lastTurn.turnIndex !== undefined
          ? lastTurn.turnIndex
          : this.turns.length - 1;
      const valid = this.turns.some(
        (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === this.selectedTurnIndex
      );
      this.selectTurn(valid ? this.selectedTurnIndex : defaultIdx);
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

    // Sort CRITICAL -> WARN -> INFO
    const priorityOrder = { CRITICAL: 0, WARN: 1, INFO: 2 };
    const sorted = [...this.violations].sort((a, b) => {
      const pA = priorityOrder[a.severity] ?? 3;
      const pB = priorityOrder[b.severity] ?? 3;
      return pA - pB;
    });

    sorted.forEach((v) => {
      const card = document.createElement('div');
      const sev = v.severity || 'INFO';
      card.className = `violation-card severity-${sev}`;

      const badgeClass = sev === 'CRITICAL' ? 'badge-critical' : sev === 'WARN' ? 'badge-warn' : 'badge-info';
      const wasteVal = v.estimated_waste_usd ?? v.estimatedWasteUSD;
      const wasteStr = wasteVal !== undefined && wasteVal !== null ? `$${Number(wasteVal).toFixed(4)} waste` : '';

      card.innerHTML = `
        <div class="violation-header">
          <div class="violation-title-group">
            <span class="badge ${badgeClass}">${sev}</span>
            <span class="violation-title">${v.title || v.rule_id || v.ruleId || 'Heuristic Alert'}</span>
          </div>
          ${wasteStr ? `<span class="violation-waste">${wasteStr}</span>` : ''}
        </div>
        <div class="violation-msg">${v.message || ''}</div>
        ${v.suggested_fix || v.suggestedFix ? `<div class="violation-fix">💡 Fix: ${v.suggested_fix || v.suggestedFix}</div>` : ''}
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

          if (targetTurn !== null && targetTurn !== undefined) {
            this.selectTurn(targetTurn);
          }

          document.getElementById('blocks-table-body')?.scrollIntoView({ behavior: 'smooth', block: 'center' });

          if (targetBlockId && this.blocksTableBody) {
            const rows = Array.from(this.blocksTableBody.querySelectorAll('tr'));
            const matchingRow = rows.find(
              (tr) =>
                tr.dataset.blockId === targetBlockId ||
                tr.querySelector('.code-cell')?.textContent.includes(targetBlockId)
            );

            if (matchingRow) {
              matchingRow.classList.remove('highlight-culprit-row');
              void matchingRow.offsetWidth;
              matchingRow.classList.add('highlight-culprit-row');
              setTimeout(() => {
                matchingRow.classList.remove('highlight-culprit-row');
              }, 2500);
            } else {
              this.showToast(`Turn #${targetTurn ?? '?'}: Culprit block ${targetBlockId} not found in active blocks`);
            }
          } else {
            const turnLabel = targetTurn !== null && targetTurn !== undefined ? `Turn #${targetTurn}` : 'Current turn';
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

    // Calculate category token aggregates (system_blocks, tool_defs, conversation_history, tool_results)
    let sysTokens = 0;
    let toolTokens = 0;
    let histTokens = 0;
    let resTokens = 0;

    const sysBlocks = turn.system_blocks || turn.systemBlocks;
    const toolBlocks = turn.tool_defs || turn.toolDefs;
    const histBlocks = turn.conversation_history || turn.conversationHistory;
    const resBlocks = turn.tool_results || turn.toolResults;
    const asstBlocks = turn.assistant_blocks || turn.assistantBlocks;

    if (sysBlocks || toolBlocks || histBlocks || resBlocks || asstBlocks) {
      if (sysBlocks) sysTokens = sysBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      if (toolBlocks) toolTokens = toolBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      if (histBlocks) histTokens += histBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      if (asstBlocks) histTokens += asstBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
      if (resBlocks) resTokens = resBlocks.reduce((acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0), 0);
    }

    // If all are 0, check all_blocks or blocks
    if (sysTokens === 0 && toolTokens === 0 && histTokens === 0 && resTokens === 0) {
      const allBlocks = turn.all_blocks || turn.blocks || [];
      allBlocks.forEach((b) => {
        const type = (b.block_type || b.blockType || '').toLowerCase();
        const count = b.token_count ?? b.tokenCount ?? 0;
        if (type.includes('system')) {
          sysTokens += count;
        } else if (type.includes('tool_def') || type.includes('tool_declaration')) {
          toolTokens += count;
        } else if (type.includes('tool_result')) {
          resTokens += count;
        } else {
          histTokens += count;
        }
      });
    }

    // Fallback to category_breakdown if still 0
    if (sysTokens === 0 && toolTokens === 0 && histTokens === 0 && resTokens === 0) {
      const cb = turn.category_breakdown || turn.categoryBreakdown || turn.token_breakdown || turn.tokenBreakdown;
      if (cb) {
        sysTokens = cb.system || 0;
        toolTokens = cb.tools || cb.tool_defs || 0;
        histTokens = cb.history || cb.conversation_history || cb.conversation || 0;
        resTokens = cb.tool_results || cb.toolResults || cb.results || 0;
      }
    }

    const totalTokens = sysTokens + toolTokens + histTokens + resTokens;

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
      { key: 'tools', name: 'Tool Defs', tokens: toolTokens, class: 'segment-tools', filter: 'TOOLS' },
      { key: 'messages', name: 'Messages / History', tokens: histTokens, class: 'segment-messages', filter: 'MESSAGES' },
      { key: 'results', name: 'Tool Results', tokens: resTokens, class: 'segment-results', filter: 'TOOL_RESULTS' },
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

  selectTurn(turnIndex) {
    this.selectedTurnIndex = turnIndex;
    const turn = this.turns.find(
      (t) => (t.turn_index !== undefined ? t.turn_index : t.turnIndex) === turnIndex
    );
    if (!turn) return;

    const tIdx = turn.turn_index !== undefined ? turn.turn_index : turn.turnIndex;
    if (this.turnTitle) {
      this.turnTitle.textContent = `Turn #${tIdx} Inspector`;
    }

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

    // Auto-diff (N vs N-1) handling
    if (tIdx === 0 || this.turns.length <= 1) {
      if (this.autoDiffRibbon) {
        this.autoDiffRibbon.innerHTML = `
          <span class="badge badge-info">Turn #0: Initial Prompt Baseline (All blocks initial load)</span>
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
        Comparing Turn #${prevIndex} → Turn #${turnIndex}...
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
      <span class="auto-diff-title">Turn #${prevIndex} → #${turnIndex} Delta:</span>
      <span class="delta-pill ${deltaClass}">${deltaFormatted}</span>
      <span class="badge badge-added" title="${added.length ? added.join(", ") : "None"}">${added.length} Added</span>
      <span class="badge badge-mutated" title="${mutated.length ? mutated.join(", ") : "None"}">${mutated.length} Mutated</span>
      <span class="badge badge-evicted" title="${evicted.length ? evicted.join(", ") : "None"}">${evicted.length} Evicted</span>
      <span class="badge badge-persisted" title="${persisted.length ? persisted.join(", ") : "None"}">${persisted.length} Persisted</span>
      ${breakpointHtml}
    `;
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
    const totalTurnTokens = blocks.reduce(
      (acc, b) => acc + (b.token_count ?? b.tokenCount ?? 0),
      0
    ) || (turn.input_tokens ?? turn.inputTokens ?? 0);

    // Filter rows based on this.currentBlockFilter
    const filter = this.currentBlockFilter || 'ALL';
    const filteredBlocks = blocks.filter((b) => {
      const bType = (b.block_type || b.blockType || '').toLowerCase();
      const status = (b.lifecycle_status || b.status || '').toLowerCase();

      switch (filter) {
        case 'SYSTEM':
          return bType.includes('system');
        case 'TOOLS':
          return bType.includes('tool_def') || bType.includes('tool_declaration');
        case 'MESSAGES':
          return bType.includes('user') || bType.includes('assistant') || bType.includes('conversation');
        case 'TOOL_RESULTS':
          return bType.includes('tool_result');
        case 'ADDED':
          return status === 'added';
        case 'MUTATED':
          return status === 'mutated';
        case 'ALL':
        default:
          return true;
      }
    });

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

    filteredBlocks.forEach((b) => {
      const row = document.createElement('tr');
      const bId = b.block_id || b.blockId || '—';
      row.dataset.blockId = bId;
      const bType = b.block_type || b.blockType || 'block';
      const identityKey = b.identity_key || b.identityKey || '';
      const status = b.lifecycle_status || b.status || '';
      const tokCount = b.token_count ?? b.tokenCount ?? 0;
      const hash = b.content_hash || b.contentHash || '';
      const hashShort = hash ? `${hash.slice(0, 8)}...` : '—';
      const survived = b.turns_survived ?? b.turnsSurvived;
      const survivedText = survived !== undefined ? `${survived} turns` : '—';

      const pctNum = totalTurnTokens > 0 ? (tokCount / totalTurnTokens) * 100 : 0;
      let pctStr = '0%';
      if (pctNum >= 1) {
        pctStr = `${Math.round(pctNum)}%`;
      } else if (pctNum > 0) {
        pctStr = '<1%';
      }
      const tokenDisplay = `${tokCount.toLocaleString()} tok (${pctStr})`;

      let statusBadge = '';
      if (status === 'added') {
        statusBadge = '<span class="badge badge-added" style="margin-left: 6px;">[+] Added</span>';
      } else if (status === 'mutated') {
        statusBadge = '<span class="badge badge-mutated" style="margin-left: 6px;">[~] Mutated</span>';
      } else if (status === 'evicted') {
        statusBadge = '<span class="badge badge-evicted" style="margin-left: 6px;">[-] Evicted</span>';
      } else if (status === 'persisted') {
        statusBadge = '<span class="badge badge-persisted" style="margin-left: 6px;">[=] Persisted</span>';
      }

      row.innerHTML = `
        <td class="code-cell">
          <div style="font-weight: 600;">${bId}</div>
          ${identityKey ? `<div style="font-size: 11px; color: var(--text-secondary); font-family: var(--font-mono);">${identityKey}</div>` : ''}
        </td>
        <td>
          <span class="badge badge-info">${bType}</span>
          ${statusBadge}
        </td>
        <td style="font-family: var(--font-mono); white-space: nowrap;">${tokenDisplay}</td>
        <td>${survivedText}</td>
        <td class="hash-cell">${hashShort}</td>
        <td>
          <button class="btn" style="padding: 2px 8px; font-size: 11px;">View Content</button>
        </td>
      `;

      const viewBtn = row.querySelector('button');
      if (viewBtn) {
        viewBtn.addEventListener('click', () => {
          this.openModal(`Block: ${bId} (${bType})`, b.content || JSON.stringify(b, null, 2));
        });
      }

      this.blocksTableBody.appendChild(row);
    });
  }

  renderEmptyTurnInspector() {
    if (this.turnTitle) this.turnTitle.textContent = 'Turn Inspector';
    if (this.turnMetaRibbon) this.turnMetaRibbon.innerHTML = '<span>Waiting for proxied agent traffic...</span>';
    if (this.autoDiffRibbon) this.autoDiffRibbon.innerHTML = '';
    if (this.contextProportionBar) this.contextProportionBar.innerHTML = '';
    if (this.blocksTableBody) {
      this.blocksTableBody.innerHTML = `
        <tr>
          <td colspan="6" style="text-align: center; color: var(--text-secondary); padding: 36px 20px; line-height: 1.6;">
            <div style="font-weight: 600; font-size: 14px; color: #e3b341; margin-bottom: 8px;">
              Notice: Unproxied requests are not detected
            </div>
            <div style="font-size: 12px; max-width: 500px; margin: 0 auto; color: var(--text-muted);">
              Automatic OS-level packet proxying without root/VPN is not supported.
              Sessions and turns appear dynamically as soon as an agent routes traffic through ctxins:
              <br><br>
              <div style="text-align: left; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 12px; font-family: monospace; font-size: 11px;">
                <div style="color: #8b949e; margin-bottom: 4px;"># Launch agent directly through ctxins proxy:</div>
                <div style="color: #58a6ff; margin-bottom: 8px;">ctxins run -- &lt;agent-command&gt;</div>
                <div style="color: #8b949e; margin-bottom: 4px;"># Or export proxy environment in your agent terminal:</div>
                <div style="color: #58a6ff; margin-bottom: 8px;">eval $(ctxins env)</div>
                <div style="color: #8b949e; margin-bottom: 4px;"># To unset proxy environment variables when finished:</div>
                <div style="color: #e3b341;">eval $(ctxins env --unset)</div>
              </div>
            </div>
          </td>
        </tr>
      `;
    }
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
      opt1.textContent = `Turn #${idx}`;
      this.diffT1.appendChild(opt1);

      const opt2 = document.createElement('option');
      opt2.value = idx;
      opt2.textContent = `Turn #${idx}`;
      this.diffT2.appendChild(opt2);
    });

    if (this.turns.length >= 2) {
      const prevTurn = this.turns[this.turns.length - 2];
      const lastTurn = this.turns[this.turns.length - 1];
      const prevIdx = prevTurn.turn_index ?? prevTurn.turnIndex ?? 0;
      const lastIdx = lastTurn.turn_index ?? lastTurn.turnIndex ?? 1;
      this.diffT1.value = currentT1 || prevIdx;
      this.diffT2.value = currentT2 || lastIdx;
    }
  }

  async computeDiff() {
    if (!this.activeSessionId || !this.diffT1 || !this.diffT2 || !this.diffResults) return;
    const t1 = this.diffT1.value;
    const t2 = this.diffT2.value;

    if (t1 === '' || t2 === '') return;

    try {
      const res = await fetch(`/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/diff/${t1}/${t2}`);
      if (res.ok) {
        const data = await res.json();
        this.renderDiffResults(data);
      } else {
        const err = await res.json();
        this.diffResults.innerHTML = `<div style="color: var(--color-critical); padding: 8px;">Error: ${err.detail || 'Diff failed'}</div>`;
      }
    } catch (err) {
      console.error('[DashboardApp] Diff calculation failed:', err);
    }
  }

  renderDiffResults(data) {
    if (!this.diffResults) return;
    const growth = data.tokenGrowth || data.token_growth || 0;
    const growthColor = growth > 0 ? 'var(--color-critical)' : 'var(--color-success)';
    const growthPrefix = growth > 0 ? '+' : '';

    const added = data.addedBlockIds || data.added_block_ids || [];
    const mutated = data.mutatedBlockIds || data.mutated_block_ids || [];
    const removed = data.removedBlockIds || data.removed_block_ids || [];
    const persisted = data.persistedBlockIds || data.persisted_block_ids || [];
    const breakpoint = data.cacheBreakpointBlockId || data.cache_breakpoint_block_id || null;

    const renderBadges = (arr, badgeClass) => {
      if (arr.length === 0) return '<span style="color: var(--text-secondary); font-size: 11px;">None</span>';
      return arr.map((id) => `<span class="badge ${badgeClass}">${id}</span>`).join(' ');
    };

    this.diffResults.innerHTML = `
      <div class="diff-card">
        <div class="diff-card-title">Token Growth</div>
        <div class="diff-card-value" style="color: ${growthColor};">${growthPrefix}${growth.toLocaleString()} tok</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Added Blocks (${added.length})</div>
        <div class="diff-badge-list">${renderBadges(added, 'badge-added')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Mutated Blocks (${mutated.length})</div>
        <div class="diff-badge-list">${renderBadges(mutated, 'badge-mutated')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Evicted Blocks (${removed.length})</div>
        <div class="diff-badge-list">${renderBadges(removed, 'badge-evicted')}</div>
      </div>
      <div class="diff-card">
        <div class="diff-card-title">Persisted Blocks (${persisted.length})</div>
        <div class="diff-badge-list">${renderBadges(persisted, 'badge-persisted')}</div>
      </div>
      ${breakpoint ? `
      <div class="diff-card">
        <div class="diff-card-title">Prefix Cache Breakpoint</div>
        <div class="diff-card-value" style="font-size: 12px; color: var(--color-warning); font-family: var(--font-mono);">${breakpoint}</div>
      </div>` : ''}
    `;
  }

  exportSession() {
    if (!this.activeSessionId) {
      alert('No active session to export.');
      return;
    }
    const exportUrl = `/api/v1/sessions/${encodeURIComponent(this.activeSessionId)}/export?format=jsonc`;
    window.location.href = exportUrl;
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
}

document.addEventListener('DOMContentLoaded', () => {
  const app = new DashboardApp();
  app.init();
  window.dashboardApp = app;
});
