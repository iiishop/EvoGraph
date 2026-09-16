import { createApp } from 'vue';
import App from './App.vue';
import '@vue-flow/core/dist/style.css';
import '@vue-flow/core/dist/theme-default.css';
import '@vue-flow/controls/dist/style.css';
import './styles.css';
import './styles/agent.css';
createApp(App).mount('#app');
document.documentElement.dataset.appVersion = '0.1.0';
