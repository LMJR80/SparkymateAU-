<script>
let deferredPrompt = null;

window.addEventListener('beforeinstallprompt', (event) => {
  event.preventDefault();
  deferredPrompt = event;
  const installBtn = document.getElementById('installApp');
  if (installBtn) {
    installBtn.style.display = 'inline-block';
  }
});

async function installPwa() {
  if (!deferredPrompt) return;
  deferredPrompt.prompt();
  const { outcome } = await deferredPrompt.userChoice;
  if (outcome === 'accepted') {
    console.log('PWA install accepted');
  }
  deferredPrompt = null;
  const installBtn = document.getElementById('installApp');
  if (installBtn) {
    installBtn.style.display = 'none';
  }
}
</script>
