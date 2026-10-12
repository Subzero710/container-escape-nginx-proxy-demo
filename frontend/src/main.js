import './style.css';

const API_URL = 'https://backend.apex.tld';
const healthResult = document.querySelector('#health-result');
const itemsList = document.querySelector('#items');
const itemsStatus = document.querySelector('#items-status');
const feedback = document.querySelector('#item-feedback');
const form = document.querySelector('#item-form');
const messageInput = document.querySelector('#message');
const submitButton = document.querySelector('#submit-item');
let healthPending = false;
let itemsPending = false;

async function api(path, options = {}) {
  const response = await fetch(API_URL + path, {
    ...options,
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) {
    throw new Error('HTTP ' + response.status + ' : ' + await response.text());
  }
  return response.json();
}

async function refreshHealth() {
  if (healthPending) return;
  healthPending = true;
  try {
    const result = await api('/health');
    healthResult.textContent = JSON.stringify(result, null, 2);
    healthResult.className = 'success';
  } catch (error) {
    healthResult.textContent = 'Échec de la vérification : ' + error.message;
    healthResult.className = 'error';
  } finally {
    healthPending = false;
  }
}

async function refreshItems() {
  if (itemsPending) return;
  itemsPending = true;
  itemsStatus.textContent = 'Chargement des items…';
  itemsStatus.className = '';
  try {
    const items = await api('/items');
    itemsList.replaceChildren();
    for (const item of items) {
      const row = document.createElement('li');
      const message = document.createElement('p');
      const details = document.createElement('small');
      message.textContent = item.message;
      details.textContent = '#' + item.id + ' · ' + new Date(item.created_at).toLocaleString();
      row.append(message, details);
      itemsList.append(row);
    }
    itemsStatus.textContent = items.length === 0
      ? 'Aucun item enregistré.'
      : items.length + ' derniers items · actualisation ' + new Date().toLocaleTimeString();
  } catch (error) {
    itemsList.replaceChildren();
    itemsStatus.textContent = 'Impossible de charger les items : ' + error.message;
    itemsStatus.className = 'error';
  } finally {
    itemsPending = false;
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) {
    feedback.textContent = 'Saisir un message non vide.';
    feedback.className = 'error';
    return;
  }
  submitButton.disabled = true;
  try {
    const item = await api('/items', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    feedback.textContent = 'Item #' + item.id + ' enregistré.';
    feedback.className = 'success';
    form.reset();
    await refreshItems();
  } catch (error) {
    feedback.textContent = 'Création impossible : ' + error.message;
    feedback.className = 'error';
  } finally {
    submitButton.disabled = false;
  }
});

document.querySelector('#refresh-health').addEventListener('click', refreshHealth);
document.querySelector('#refresh-items').addEventListener('click', refreshItems);
void Promise.all([refreshHealth(), refreshItems()]);
setInterval(() => { void Promise.all([refreshHealth(), refreshItems()]); }, 15000);
