// ========================================================
// Searchable Coin Selector Component for Laya Trade
// ========================================================

const COIN_LIST = [
  // Major Mega Caps (Top Default)
  { symbol: "BTCUSDT.P", name: "Bitcoin", icon: "₿", category: "major", catName: "Major", badge: "Core" },
  { symbol: "ETHUSDT.P", name: "Ethereum", icon: "Ξ", category: "major", catName: "Major", badge: "Core" },
  { symbol: "SOLUSDT.P", name: "Solana", icon: "◎", category: "major", catName: "Major", badge: "Top" },
  { symbol: "BNBUSDT.P", name: "BNB", icon: "🟡", category: "major", catName: "Major", badge: "" },
  { symbol: "XRPUSDT.P", name: "XRP", icon: "✕", category: "major", catName: "Major", badge: "" },

  // Top Meme Coins
  { symbol: "1000PEPEUSDT.P", name: "Pepe", icon: "🐸", category: "meme", catName: "Meme", badge: "Hot" },
  { symbol: "DOGEUSDT.P", name: "Dogecoin", icon: "Ð", category: "meme", catName: "Meme", badge: "Top" },
  { symbol: "1000SHIBUSDT.P", name: "Shiba Inu", icon: "🐕", category: "meme", catName: "Meme", badge: "" },
  { symbol: "1000BONKUSDT.P", name: "Bonk", icon: "🦴", category: "meme", catName: "Meme", badge: "" },
  { symbol: "1000FLOKIUSDT.P", name: "Floki", icon: "⚔️", category: "meme", catName: "Meme", badge: "" },
  { symbol: "WIFUSDT.P", name: "dogwifhat", icon: "🧢", category: "meme", catName: "Meme", badge: "Solana" },
  { symbol: "POPCATUSDT.P", name: "Popcat", icon: "🐱", category: "meme", catName: "Meme", badge: "" },
  { symbol: "NEIROUSDT.P", name: "Neiro", icon: "🐶", category: "meme", catName: "Meme", badge: "New" },
  { symbol: "TURBOUSDT.P", name: "Turbo", icon: "🏎️", category: "meme", catName: "Meme", badge: "AI Meme" },

  // Layer 1 & Layer 2
  { symbol: "SUIUSDT.P", name: "Sui", icon: "💧", category: "l1", catName: "L1/L2", badge: "Vol" },
  { symbol: "AVAXUSDT.P", name: "Avalanche", icon: "🔺", category: "l1", catName: "L1/L2", badge: "" },
  { symbol: "ADAUSDT.P", name: "Cardano", icon: "₳", category: "l1", catName: "L1/L2", badge: "" },
  { symbol: "NEARUSDT.P", name: "NEAR Protocol", icon: "Ⓝ", category: "l1", catName: "L1/L2", badge: "" },
  { symbol: "TONUSDT.P", name: "Toncoin", icon: "💎", category: "l1", catName: "L1/L2", badge: "" },
  { symbol: "ARBUSDT.P", name: "Arbitrum", icon: "🔵", category: "l1", catName: "L1/L2", badge: "L2" },
  { symbol: "OPUSDT.P", name: "Optimism", icon: "🔴", category: "l1", catName: "L1/L2", badge: "L2" },
  { symbol: "APTUSDT.P", name: "Aptos", icon: "🌐", category: "l1", catName: "L1/L2", badge: "" },
  { symbol: "SEIUSDT.P", name: "Sei", icon: "🔴", category: "l1", catName: "L1/L2", badge: "" },
  { symbol: "DOTUSDT.P", name: "Polkadot", icon: "●", category: "l1", catName: "L1/L2", badge: "" },
  { symbol: "LTCUSDT.P", name: "Litecoin", icon: "Ł", category: "l1", catName: "L1/L2", badge: "" },

  // AI & Infrastructure
  { symbol: "TAOUSDT.P", name: "Bittensor", icon: "🧠", category: "ai", catName: "AI/Infra", badge: "AI" },
  { symbol: "FETUSDT.P", name: "Artificial Superintelligence", icon: "🤖", category: "ai", catName: "AI/Infra", badge: "AI" },
  { symbol: "RENDERUSDT.P", name: "Render", icon: "🎨", category: "ai", catName: "AI/Infra", badge: "GPU" },
  { symbol: "LINKUSDT.P", name: "Chainlink", icon: "🔗", category: "ai", catName: "AI/Infra", badge: "Oracle" },
  { symbol: "TIAUSDT.P", name: "Celestia", icon: "🧱", category: "ai", catName: "AI/Infra", badge: "DA" },
  { symbol: "INJUSDT.P", name: "Injective", icon: "⚡", category: "ai", catName: "AI/Infra", badge: "DeFi" },
  { symbol: "KASUSDT.P", name: "Kaspa", icon: "🪙", category: "ai", catName: "AI/Infra", badge: "PoW" }
];

export function getCoinMetadata(symbol) {
  if (!symbol) return null;
  const cleanSym = symbol.toUpperCase().trim();
  return (
    COIN_LIST.find((c) => c.symbol.toUpperCase() === cleanSym) || {
      symbol: cleanSym,
      name: cleanSym.replace("USDT.P", "").replace("1000", ""),
      icon: "🪙",
      category: "other",
      catName: "Perpetual",
      badge: ""
    }
  );
}

export function initCoinSearch({ onSelect, getActiveSymbol }) {
  const triggerBtn = document.getElementById("coinSelectorBtn");
  const modal = document.getElementById("coinSearchModal");
  const closeBtn = document.getElementById("btnCloseCoinSearch");
  const searchInput = document.getElementById("coinSearchInput");
  const clearBtn = document.getElementById("coinSearchClear");
  const listContainer = document.getElementById("coinListContainer");
  const countDisplay = document.getElementById("coinCountDisplay");
  const tabButtons = document.querySelectorAll(".coin-tab-btn");
  const triggerIcon = document.getElementById("coinBtnIcon");
  const triggerSymbol = document.getElementById("coinBtnSymbol");
  const nativeSelect = document.getElementById("symbolSelect");

  let activeCategory = "all";
  let searchQuery = "";
  let focusedIndex = -1;
  let isOpen = false;

  function updateTriggerDisplay(symbol) {
    const meta = getCoinMetadata(symbol);
    if (meta) {
      if (triggerIcon) triggerIcon.textContent = meta.icon;
      if (triggerSymbol) triggerSymbol.textContent = meta.symbol;
    }
  }

  function openModal() {
    if (isOpen) return;
    isOpen = true;
    if (modal) modal.showModal();
    if (triggerBtn) {
      triggerBtn.setAttribute("aria-expanded", "true");
      triggerBtn.classList.add("active");
    }

    // Reset search query & focus input
    searchQuery = "";
    if (searchInput) {
      searchInput.value = "";
      setTimeout(() => searchInput.focus(), 60);
    }
    if (clearBtn) clearBtn.classList.add("hide");

    // Render fresh list
    focusedIndex = -1;
    renderList();
  }

  function closeModal() {
    if (modal?.open) modal.close(); // the "close" listener below resets state
  }

  // Runs for every close path, including the dialog's native Esc handling.
  function onDialogClose() {
    isOpen = false;
    if (triggerBtn) {
      triggerBtn.setAttribute("aria-expanded", "false");
      triggerBtn.classList.remove("active");
    }
    focusedIndex = -1;
  }

  function toggleModal() {
    if (isOpen) {
      closeModal();
    } else {
      openModal();
    }
  }

  function highlightMatches(text, query) {
    if (!query) return escapeHtml(text);
    const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const regex = new RegExp(`(${escapedQuery})`, "gi");
    return escapeHtml(text).replace(regex, '<mark class="search-highlight">$1</mark>');
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function getFilteredCoins() {
    const q = searchQuery.toLowerCase().trim();
    return COIN_LIST.filter((coin) => {
      // Category filter
      if (activeCategory !== "all" && coin.category !== activeCategory) {
        return false;
      }
      // Text search query filter (matches ticker, name, or category)
      if (q) {
        const matchSymbol = coin.symbol.toLowerCase().includes(q);
        const matchName = coin.name.toLowerCase().includes(q);
        const matchCat = coin.catName.toLowerCase().includes(q);
        const matchClean = coin.symbol.replace("USDT.P", "").replace("1000", "").toLowerCase().includes(q);
        return matchSymbol || matchName || matchCat || matchClean;
      }
      return true;
    });
  }

  function renderList() {
    if (!listContainer) return;
    const filtered = getFilteredCoins();
    const currentActive = getActiveSymbol ? getActiveSymbol() : (nativeSelect ? nativeSelect.value : "BTCUSDT.P");

    if (countDisplay) {
      if (searchQuery) {
        countDisplay.textContent = `${filtered.length} match${filtered.length === 1 ? "" : "es"}`;
      } else {
        countDisplay.textContent = `${COIN_LIST.length} perpetual pairs`;
      }
    }

    if (filtered.length === 0) {
      listContainer.innerHTML = `
        <div class="coin-empty-state">
          <i data-lucide="search-x" class="coin-empty-icon"></i>
          <p>No pairs found matching "<strong>${escapeHtml(searchQuery)}</strong>"</p>
          <span class="coin-empty-sub">Try searching another token name like BTC, SOL, or PEPE</span>
        </div>
      `;
      if (window.lucide) window.lucide.createIcons();
      return;
    }

    // Grouping by category when no search query is active
    let html = "";
    if (!searchQuery && activeCategory === "all") {
      const categories = [
        { id: "major", title: "👑 Major Mega Caps" },
        { id: "meme", title: "🔥 Top Meme Coins" },
        { id: "l1", title: "⚡ Layer 1 & Layer 2" },
        { id: "ai", title: "🤖 AI & Infrastructure" }
      ];

      categories.forEach((cat) => {
        const groupCoins = filtered.filter((c) => c.category === cat.id);
        if (groupCoins.length > 0) {
          html += `<div class="coin-group-label">${cat.title} <span>${groupCoins.length}</span></div>`;
          groupCoins.forEach((coin) => {
            html += renderCoinItem(coin, currentActive, searchQuery);
          });
        }
      });
    } else {
      filtered.forEach((coin) => {
        html += renderCoinItem(coin, currentActive, searchQuery);
      });
    }

    listContainer.innerHTML = html;
    if (window.lucide) window.lucide.createIcons();

    // Re-bind click events
    listContainer.querySelectorAll(".coin-item").forEach((el) => {
      el.addEventListener("click", () => {
        const sym = el.getAttribute("data-symbol");
        handleCoinSelect(sym);
      });
    });

    updateFocusedItem();
  }

  function renderCoinItem(coin, currentActive, query) {
    const isSelected = coin.symbol === currentActive;
    const symbolHtml = highlightMatches(coin.symbol, query);
    const nameHtml = highlightMatches(coin.name, query);

    return `
      <div 
        class="coin-item ${isSelected ? "active" : ""}" 
        data-symbol="${coin.symbol}" 
        role="option" 
        aria-selected="${isSelected ? "true" : "false"}"
      >
        <div class="coin-item-left">
          <span class="coin-item-icon">${coin.icon}</span>
          <div class="coin-item-info">
            <span class="coin-item-symbol">${symbolHtml}</span>
            <span class="coin-item-name">${nameHtml}</span>
          </div>
        </div>
        <div class="coin-item-right">
          ${coin.badge ? `<span class="coin-item-badge">${coin.badge}</span>` : ""}
          <span class="coin-item-cat">${coin.catName}</span>
          <i data-lucide="check" class="coin-item-check"></i>
        </div>
      </div>
    `;
  }

  function handleCoinSelect(symbol) {
    updateTriggerDisplay(symbol);
    closeModal();

    if (nativeSelect) {
      nativeSelect.value = symbol;
      nativeSelect.dispatchEvent(new Event("change"));
    }

    if (onSelect) {
      onSelect(symbol);
    }
  }

  function updateFocusedItem() {
    const items = listContainer.querySelectorAll(".coin-item");
    items.forEach((item, idx) => {
      if (idx === focusedIndex) {
        item.classList.add("focused");
        item.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } else {
        item.classList.remove("focused");
      }
    });
  }

  // Event Listeners
  if (triggerBtn) {
    triggerBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleModal();
    });
  }

  if (closeBtn) {
    closeBtn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      closeModal();
    });
  }

  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      searchQuery = e.target.value;
      if (clearBtn) {
        clearBtn.classList.toggle("hide", !searchQuery);
      }
      focusedIndex = 0;
      renderList();
    });

    searchInput.addEventListener("keydown", (e) => {
      const items = listContainer.querySelectorAll(".coin-item");
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (items.length > 0) {
          focusedIndex = (focusedIndex + 1) % items.length;
          updateFocusedItem();
        }
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        if (items.length > 0) {
          focusedIndex = (focusedIndex - 1 + items.length) % items.length;
          updateFocusedItem();
        }
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (items.length > 0) {
          const target = focusedIndex >= 0 && items[focusedIndex] ? items[focusedIndex] : items[0];
          if (target) {
            const sym = target.getAttribute("data-symbol");
            handleCoinSelect(sym);
          }
        }
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      searchQuery = "";
      if (searchInput) {
        searchInput.value = "";
        searchInput.focus();
      }
      clearBtn.classList.add("hide");
      renderList();
    });
  }

  // Category Tabs
  tabButtons.forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      tabButtons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      activeCategory = btn.getAttribute("data-category") || "all";
      focusedIndex = -1;
      renderList();
    });
  });

  // Click outside to close (backdrop click)
  if (modal) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) {
        closeModal();
      }
    });
    modal.addEventListener("close", onDialogClose);
  }

  // Global hotkeys
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) {
      e.preventDefault();
      openModal();
    } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
      e.preventDefault();
      openModal();
    }
  });

  // Initial display setup
  const initSym = getActiveSymbol ? getActiveSymbol() : (nativeSelect ? nativeSelect.value : "BTCUSDT.P");
  updateTriggerDisplay(initSym);

  return {
    updateActiveCoinDisplay: updateTriggerDisplay,
    open: openModal,
    close: closeModal,
    toggle: toggleModal,
    isOpen: () => isOpen
  };
}
