/* Active AgriNex account portal -> Supabase Auth.
   Uses only the browser-safe publishable key. Auth roles and profiles are validated
   against agrinex_profiles; no service-role key is exposed to the browser. */
(function () {
  'use strict';

  const URL = 'https://ybfhsgofurowfuxmtvbt.supabase.co';
  const KEY = 'sb_publishable_AXO3OgpPkPheElo7HW6INQ_2CPpsp5L';
  const ACCESS = 'ff_sb_access_token';
  const REFRESH = 'ff_sb_refresh_token';
  const PROFILE = 'ff_sb_profile';

  const get = (id) => document.getElementById(id);
  const cacheSet = (key, value) => { if (window.storage) storage.set(key, value); };
  const cacheGet = (key) => window.storage ? storage.get(key, '') : '';
  const cacheRemove = (key) => { if (window.storage) storage.remove(key); };

  function phone(value) {
    const digits = String(value || '').replace(/\\D/g, '');
    if (digits.length === 10) return '+91' + digits;
    if (digits.length === 12 && digits.startsWith('91')) return '+' + digits;
    throw new Error('Enter a valid 10-digit Indian mobile number.');
  }

  async function supabase(path, method, body, accessToken) {
    const headers = { apikey: KEY, 'Content-Type': 'application/json' };
    if (accessToken) headers.Authorization = 'Bearer ' + accessToken;
    const response = await fetch(URL + path, {
      method: method || 'GET',
      headers,
      ...(body === undefined ? {} : { body: JSON.stringify(body) })
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const message = result.msg || result.message || result.error_description ||
        result.error || result.hint || 'Supabase request failed (' + response.status + ').';
      throw new Error(message);
    }
    return result;
  }

  function showStatus(role, mode, message, ok) {
    const id = role + (mode === 'register' ? 'Register' : 'Login') + 'Status';
    const node = get(id);
    if (!node) return;
    node.textContent = message || '';
    node.classList.toggle('ok', !!ok);
  }

  function setBusy(role, mode, busy) {
    const prefix = role === 'customer' ? 'customer' : 'farmer';
    const form = get(prefix + (mode === 'register' ? 'Register' : 'Login') + 'Form');
    const button = form && form.querySelector('.auth-submit');
    if (button) {
      button.disabled = !!busy;
      button.style.opacity = busy ? '.7' : '';
    }
  }

  async function readProfile(userId, accessToken) {
    const rows = await supabase(
      '/rest/v1/agrinex_profiles?id=eq.' + encodeURIComponent(userId) +
      '&select=id,phone,full_name,role,requested_role,farm_name,farmer_status',
      'GET', undefined, accessToken
    );
    if (!Array.isArray(rows) || !rows.length) {
      throw new Error('Your AgriNex profile was not created. Please contact the site administrator.');
    }
    return rows[0];
  }

  function useProfile(profile, session) {
    cacheSet(ACCESS, session.access_token);
    cacheSet(REFRESH, session.refresh_token || '');
    cacheSet(PROFILE, profile);
    cacheSet('ff_access_token', session.access_token);
    currentUser = {
      id: profile.id,
      name: profile.full_name,
      contact: profile.phone,
      role: profile.role,
      requestedRole: profile.requested_role,
      farmName: profile.farm_name || '',
      farmerStatus: profile.farmer_status,
      signedInAt: new Date().toISOString()
    };
    cacheSet('ff_user', currentUser);
    if (typeof updateProfileUI === 'function') updateProfileUI();
    if (typeof renderAccountActions === 'function') renderAccountActions();
  }

  async function completeSignIn(authResult, expectedRole, statusMode) {
    if (!authResult || !authResult.access_token || !authResult.user) {
      throw new Error('Authentication is incomplete. Check whether phone verification is enabled.');
    }
    const profile = await readProfile(authResult.user.id, authResult.access_token);
    if (profile.role !== expectedRole) {
      throw new Error('This number is registered as a ' + profile.role +
        ' account. Choose the matching account type.');
    }

    useProfile(profile, authResult);
    closeAuthPortal();

    if (expectedRole === 'farmer') {
      if (typeof window.ffSupabase?.loadFarmerProducts === 'function') {
        await window.ffSupabase.loadFarmerProducts();
      }
      showPage('farmer');
      if (profile.farmer_status !== 'approved') {
        toast('Farmer account created. Your listing access is pending administrator approval.');
      } else {
        toast('Farmer sign-in successful.');
      }
    } else {
      showPage('home');
      toast('Customer sign-in successful.');
    }
  }

  window.authLogin = async function (event, role) {
    event?.preventDefault();
    if (!['customer', 'farmer'].includes(role)) return false;
    const prefix = role === 'customer' ? 'customer' : 'farmer';
    const phoneInput = get(prefix + 'LoginPhone');
    const passwordInput = get(prefix + 'LoginPassword');
    showStatus(role, 'login', '', false);
    setBusy(role, 'login', true);

    try {
      const mobile = phone(phoneInput?.value);
      const password = passwordInput?.value || '';
      if (password.length < 8) throw new Error('Password must contain at least 8 characters.');
      const result = await supabase('/auth/v1/token?grant_type=password', 'POST', {
        phone: mobile,
        password
      });
      await completeSignIn(result, role, 'login');
    } catch (error) {
      showStatus(role, 'login', error.message || 'Unable to sign in.', false);
      console.error('AgriNex sign-in failed:', error);
    } finally {
      setBusy(role, 'login', false);
    }
    return false;
  };

  function addOtpVerification(role, mobile) {
    const node = get(role + 'RegisterStatus');
    if (!node) return;
    node.textContent = 'A verification code was requested for ' + mobile + '. Enter the SMS code below.';
    node.classList.remove('ok');

    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'display:flex;gap:8px;flex-wrap:wrap;margin-top:10px';
    const input = document.createElement('input');
    input.type = 'text';
    input.inputMode = 'numeric';
    input.autocomplete = 'one-time-code';
    input.maxLength = 8;
    input.placeholder = 'SMS verification code';
    input.setAttribute('aria-label', 'SMS verification code');
    input.style.cssText = 'flex:1;min-width:160px;border:1px solid #b8d9c0;border-radius:9px;padding:10px;font:inherit';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Verify mobile';
    button.style.cssText = 'border:0;border-radius:9px;background:#146b38;color:white;font-weight:800;padding:10px 14px;cursor:pointer';
    button.addEventListener('click', async function () {
      const token = input.value.trim();
      if (!token) {
        node.firstChild.textContent = 'Enter the SMS verification code.';
        return;
      }
      button.disabled = true;
      try {
        const verified = await supabase('/auth/v1/verify', 'POST', {
          phone: mobile,
          token,
          type: 'sms'
        });
        await completeSignIn(verified, role, 'register');
        wrapper.remove();
      } catch (error) {
        node.firstChild.textContent = error.message || 'Verification failed.';
        button.disabled = false;
      }
    });
    wrapper.append(input, button);
    node.appendChild(wrapper);
    input.focus();
  }

  window.authRegister = async function (event, role) {
    event?.preventDefault();
    if (!['customer', 'farmer'].includes(role)) return false;
    const prefix = role === 'customer' ? 'customer' : 'farmer';
    const name = (get(prefix + 'RegisterName')?.value || '').trim();
    const mobileInput = get(prefix + 'RegisterPhone');
    const password = get(prefix + 'RegisterPassword')?.value || '';
    const farmName = role === 'farmer' ? (get('farmerRegisterFarm')?.value || '').trim() : '';

    showStatus(role, 'register', '', false);
    setBusy(role, 'register', true);
    try {
      if (name.length < 2) throw new Error('Enter your full name.');
      const mobile = phone(mobileInput?.value);
      if (password.length < 8) throw new Error('Choose a password with at least 8 characters.');
      if (role === 'farmer' && !farmName) throw new Error('Enter your farm name.');

      const result = await supabase('/auth/v1/signup', 'POST', {
        phone: mobile,
        password,
        data: {
          full_name: name,
          requested_role: role,
          farm_name: role === 'farmer' ? farmName : null
        }
      });

      if (result.access_token && result.user) {
        await completeSignIn(result, role, 'register');
      } else if (result.user) {
        setBusy(role, 'register', false);
        addOtpVerification(role, mobile);
      } else {
        throw new Error('Supabase did not return an account. Please retry.');
      }
    } catch (error) {
      showStatus(role, 'register', error.message || 'Account creation failed.', false);
      console.error('AgriNex registration failed:', error);
    } finally {
      setBusy(role, 'register', false);
    }
    return false;
  };

  // If a session has already been saved, restore it and its server-side profile.
  async function restoreSession() {
    const refresh = cacheGet(REFRESH);
    if (!refresh) return;
    try {
      const session = await supabase('/auth/v1/token?grant_type=refresh_token', 'POST', {
        refresh_token: refresh
      });
      const profile = await readProfile(session.user.id, session.access_token);
      useProfile(profile, session);
    } catch (error) {
      [ACCESS, REFRESH, PROFILE, 'ff_access_token', 'ff_user'].forEach(cacheRemove);
      currentUser = null;
      if (typeof updateProfileUI === 'function') updateProfileUI(true);
      console.warn('AgriNex session restore failed:', error);
    }
  }

  window.ffPortalAuth = { restoreSession };
  document.addEventListener('DOMContentLoaded', restoreSession);
  setTimeout(restoreSession, 0);
})();