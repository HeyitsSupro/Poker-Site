import { mount } from 'svelte';
import './app.css';
import App from './App.svelte';
import { SITE_NAME } from './config.js';

document.title = SITE_NAME;

export default mount(App, { target: document.getElementById('app') });
