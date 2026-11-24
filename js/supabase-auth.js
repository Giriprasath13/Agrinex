/* AgriNex Supabase Auth integration.
 * Uses the public publishable key only. Never place a Supabase secret key in browser code.
 */
(function () {
  'use strict';

  const SUPABASE_URL = 'https://ybfhsgofurowfuxmtvbt.supabase.co';
  const SUPABASE_KEY = 'sb_publishable_AXO3OgpPkPheElo7HW6INQ_2CPpsp5L';
  const ACCESS_KEY = 'ff_sb_access_token';
  const REFRESH_KEY = 'ff_sb_refresh_token';
  const PROFILE_KEY = 'ff_sb_profile';

  const el = (id) => document.getElementById(id);
  const read = (key) => (window.storage && storage.get(key, '')) || '';
  const write = (key, value) => window.storage && storage.set(key, value);
  const clear = (key) => window.storage && storage.remove(key);

  function phoneE164(value) {
    const digits = String(value || '').replace(/\D/g, '');
    if (digits.length === 10) return '+91' + digits;
    if (digits.length === 12 && digits.startsWith('91')) return '+' + digits;
    if (String(value || '').trim().startsWith('+') && digits.length >= 10) return '+' + digits;
    throw new Error('Enter a valid Indian mobile number with 10 digits.');
  }

  async function request(path, options = {}, token = '') {
    const headers = {
      apikey: SUPABASE_KEY,
      'Content-Type': 'application/json',
      ...(options.headers || {})
    };
    if (token) headers.Authorization = 'Bearer ' + token;
    const response = await fetch(SUPABASE_URL + path, { ...options, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = data.msg || data.message || data.error_description || data.error || 'Supabase request failed (' + response.status + ')';
      throw new Error(message);
    }
    return data;
  }

  function saveSession(session) {
    if (!session || !session.access_token) throw new Error('Authentication succeeded without an active session. Check phone verification.');
    write(ACCESS_KEY, session.access_token);
    if (session.refresh_token) write(REFRESH_KEY, session.refresh_token);
  }

  async function getProfile(userId, token) {
    const rows = await request(
      '/rest/v1/agrinex_profiles?id=eq.' + encodeURIComponent(userId) + '&select=*',
      { method: 'GET' },
      token
    );
    if (!Array.isArray(rows) || !rows[0]) throw new Error('Your AgriNex profile was not created. Contact the project administrator.');
    return rows[0];
  }

  function applySignedInUser(profile) {
    write(PROFILE_KEY, profile);
    window.currentUser = {
      id: profile.id,
      name: profile.full_name,
      contact: profile.phone,
      role: profile.role,
      requestedRole: profile.requested_role,
      farmerStatus: profile.farmer_status,
      farmName: profile.farm_name || '',
      signedInAt: new Date().toISOString()
    };
    write('ff_user', window.currentUser);
    if (typeof updateProfileUI === 'function') updateProfileUI();
    if (typeof renderAccountActions === 'function') renderAccountActions();
    const pending = profile.requested_role === 'farmer' && profile.role !== 'farmer';
    if (pending) {
      toast('Farmer request received. Inventory access starts after administrator approval.');
    }
  }

  async function finishLogin(session) {
    saveSession(session);
    const profile = await getProfile(session.user.id, session.access_token);
    applySignedInUser(profile);
    if (el('loginModal')) closeModals();
    if (profile.role === 'farmer' && profile.farmer_status === 'approved') {
      showPage('farmer');
    } else if (profile.requested_role === 'farmer') {
      showPage('home');
    }
    toast('Signed in to AgriNex');
  }

  window.registerBackendUser = async function () {
    try {
      const phone = phoneE164(el('loginContact')?.value);
      const password = el('loginPassword')?.value || '';
      const role = el('loginRole')?.value || 'customer';
      const fullName = (el('loginFullName')?.value || '').trim();
      const farmName = (el('loginFarmName')?.value || '').trim();

      if (password.length < 8) throw new Error('Password must contain at least 8 characters.');
      if (!fullName) throw new Error('Enter your full name.');
      if (role === 'farmer' && !farmName) throw new Error('Enter your farm name.');

      const data = await request('/auth/v1/signup', {
        method: 'POST',
        body: JSON.stringify({
          phone,
          password,
          data: {
            full_name: fullName,
            requested_role: role,
            farm_name: role === 'farmer' ? farmName : null
          }
        })
      });

      if (data.access_token) {
        await finishLogin(data);
        return;
      }

      const otp = prompt('Enter the SMS verification code sent to ' + phone + ':');
      if (!otp) {
        toast('Account request started. Verify the SMS code to finish signing in.');
        return;
      }
      const verified = await request('/auth/v1/verify', {
        method: 'POST',
        body: JSON.stringify({ phone, token: otp.trim(), type: 'sms' })
      });
      await finishLogin(verified);
    } catch (error) {
      toast(error.message || 'Could not create your account.');
      console.error('AgriNex signup error:', error);
    }
  };

  window.loginDemo = async function () {
    try {
      const phone = phoneE164(el('loginContact')?.value);
      const password = el('loginPassword')?.value || '';
      if (password.length < 8) throw new Error('Password must contain at least 8 characters.');
      const data = await request('/auth/v1/token?grant_type=password', {
        method: 'POST',
        body: JSON.stringify({ phone, password })
      });
      await finishLogin(data);
    } catch (error) {
      toast(error.message || 'Login failed. Check the mobile number and password.');
      console.error('AgriNex login error:', error);
    }
  };

  window.logoutDemo = async function () {
    const token = read(ACCESS_KEY);
    if (token) {
      try { await request('/auth/v1/logout', { method: 'POST' }, token); } catch (error) { console.warn('Sign-out request:', error); }
    }
    [ACCESS_KEY, REFRESH_KEY, PROFILE_KEY, 'ff_user', 'ff_access_token'].forEach(clear);
    window.currentUser = null;
    if (typeof updateProfileUI === 'function') updateProfileUI(true);
    if (typeof closeModals === 'function') closeModals();
    toast('Signed out.');
  };
  window.authSignOutAndSwitch = window.logoutDemo;

  async function restoreSession() {
    const refresh = read(REFRESH_KEY);
    if (!refresh) return;
    try {
      const session = await request('/auth/v1/token?grant_type=refresh_token', {
        method: 'POST',
        body: JSON.stringify({ refresh_token: refresh })
      });
      saveSession(session);
      const profile = await getProfile(session.user.id, session.access_token);
      applySignedInUser(profile);
    } catch (error) {
      [ACCESS_KEY, REFRESH_KEY, PROFILE_KEY].forEach(clear);
      console.warn('AgriNex session restore:', error);
    }
  }

  async function loadFarmerProducts() {
    const profile = window.currentUser;
    const token = read(ACCESS_KEY);
    if (!profile || profile.role !== 'farmer' || !token) return;
    try {
      const rows = await request(
        '/rest/v1/agrinex_products?farmer_id=eq.' + encodeURIComponent(profile.id) + '&select=*&order=created_at.desc',
        { method: 'GET' },
        token
      );
      rows.forEach((row) => {
        const item = {
          id: 'api-' + row.id, serverId: row.id, name: row.name, category: row.category,
          description: row.description || '', price: Number(row.price), market: Number(row.market_price),
          farmer: row.farmer_name, farmerId: row.farmer_id, emoji: '🌱', rating: 0,
          distance: 0, stock: Number(row.stock), organic: !!row.organic, fresh: 'Available',
          verified: !!row.farmer_verified, harvest: 0, delivery: 'Scheduled', hours: 1,
          image: row.image_url || '', unit: row.unit || 'kg', location: row.district || '',
          status: row.status
        };
        const existing = (window.products || []).findIndex((p) => p.serverId === row.id);
        if (existing >= 0) products[existing] = item;
        else products.unshift(item);
      });
      if (typeof renderInventory === 'function') renderInventory();
      if (typeof renderMarketplace === 'function') renderMarketplace();
    } catch (error) {
      console.error('Could not load farmer inventory:', error);
      toast('Could not load cloud inventory: ' + error.message);
    }
  }

  const originalAddProduct = window.addProduct;
  window.addProduct = async function () {
    const profile = window.currentUser;
    if (!profile || profile.role !== 'farmer') {
      if (profile && profile.requestedRole === 'farmer') {
        toast('Farmer account approval is pending. You cannot publish listings yet.');
      } else {
        toast('Sign in with an approved farmer account to publish products.');
      }
      return;
    }
    const token = read(ACCESS_KEY);
    if (!token) { toast('Your session expired. Please sign in again.'); return; }

    const name = (el('newName')?.value || '').trim();
    const price = Number(el('newPrice')?.value);
    const market = Number(el('newMarket')?.value) || price;
    const stock = Number(el('newStock')?.value);
    if (!name || !(price > 0) || !(stock >= 0)) {
      toast('Enter a product name, valid price, and stock quantity.');
      return;
    }
    const category = el('newCategory')?.value || 'Vegetables';
    const unit = el('newUnit')?.value || 'kg';
    const imageUrl = (el('newImage')?.value || '').trim();
    try {
      const rows = await request('/rest/v1/agrinex_products', {
        method: 'POST',
        headers: { Prefer: 'return=representation' },
        body: JSON.stringify({
          farmer_id: profile.id,
          farmer_name: profile.name,
          farmer_farm_name: profile.farmName,
          name, category, description: '',
          price, market_price: market, stock, unit,
          organic: category === 'Organic',
          image_url: imageUrl || null,
          status: 'draft',
          farmer_verified: false
        })
      }, token);
      const row = rows[0];
      products.unshift({
        id: 'api-' + row.id, serverId: row.id, name: row.name, category: row.category,
        description: '', price: Number(row.price), market: Number(row.market_price),
        farmer: row.farmer_name, farmerId: row.farmer_id, emoji: '🌱', rating: 0,
        distance: 0, stock: Number(row.stock), organic: !!row.organic, fresh: 'Available',
        verified: false, harvest: 0, delivery: 'Scheduled', hours: 1,
        image: row.image_url || '', unit: row.unit || 'kg', location: '', status: row.status
      });
      if (typeof persist === 'function') persist();
      closeModals();
      if (typeof renderInventory === 'function') renderInventory();
      if (typeof renderMarketplace === 'function') renderMarketplace();
      toast('Product saved to Supabase as a draft. Admin approval is required before public listing.');
    } catch (error) {
      toast('Could not save product: ' + error.message);
      console.error('AgriNex product insert:', error);
    }
  };

  window.ffSupabase = {
    url: SUPABASE_URL,
    isSignedIn: () => !!read(ACCESS_KEY),
    loadFarmerProducts,
    restoreSession
  };

  const role = el('loginRole');
  if (role) role.dispatchEvent(new Event('change', { bubbles: true }));
  setTimeout(restoreSession, 0);
  setTimeout(loadFarmerProducts, 800);
})();
