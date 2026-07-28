import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { VRMLoaderPlugin, VRMUtils } from '@pixiv/three-vrm';
import { VRMAnimationLoaderPlugin, createVRMAnimationClip } from 'https://cdn.jsdelivr.net/npm/@pixiv/three-vrm-animation@2.1.0/lib/three-vrm-animation.module.js';

// ==========================================
// CONFIG
// ==========================================
const VRM_PATH = '';
const VRMA_PATH = '';
const PYTHON_SERVER = 'http://127.0.0.1:8765';

// ==========================================
// SCENE SETUP
// ==========================================
const container = document.getElementById('canvas-container');
const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setClearColor(0x000000, 0);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.outputColorSpace = THREE.SRGBColorSpace;
container.appendChild(renderer.domElement);

const scene = new THREE.Scene();

const camera = new THREE.PerspectiveCamera(30, window.innerWidth / window.innerHeight, 0.1, 20);
camera.position.set(0, 1.4, 2.5);

const ambient = new THREE.AmbientLight(0xffffff, 0.8);
scene.add(ambient);
const dirLight = new THREE.DirectionalLight(0xfff0f0, 1.2);
dirLight.position.set(1, 2, 1);
scene.add(dirLight);
const fillLight = new THREE.DirectionalLight(0xf0f0ff, 0.6);
fillLight.position.set(-1, 1, -1);
scene.add(fillLight);

// OrbitControls bleibt deaktiviert, da sie sonst mit dem Charakter-Dragging
// unten um die gleichen Maus-Events konkurrieren würde.
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(0, 1.2, 0);
controls.update();
controls.enabled = false;

// ==========================================
// VRM LADEN
// ==========================================
let currentVrm = null;
let mixer = null;

const loader = new GLTFLoader();
loader.register(parser => new VRMLoaderPlugin(parser));
loader.register(parser => new VRMAnimationLoaderPlugin(parser));

loader.load(VRM_PATH,
    (gltf) => {
        const vrm = gltf.userData?.vrm;
        if (!vrm) {
            console.error('VRM plugin did not attach VRM data. Falling back to the raw GLTF scene.', gltf);
            scene.add(gltf.scene);
            return;
        }

        VRMUtils.removeUnnecessaryVertices(gltf.scene);
        VRMUtils.removeUnnecessaryJoints(gltf.scene);

        const model = vrm.scene;
        model.position.set(0, -0.5, 0);
        model.rotation.y = 0;
        model.scale.setScalar(1.0);
        scene.add(model);

        currentVrm = vrm;
        camera.lookAt(0, -1.0, 0);
        controls.target.set(0, -0.5, 0);
        controls.update();

        console.log('✅ VRM geladen!');

        loader.load(VRMA_PATH, (animGltf) => {
            const vrmAnimation = animGltf.userData.vrmAnimations[0];
            if (vrmAnimation) {
                mixer = new THREE.AnimationMixer(vrm.scene);
                vrm.scene.position.y = -0.9;
                const clip = createVRMAnimationClip(vrmAnimation, vrm);
                mixer.clipAction(clip).play();
                console.log('✅ Animation geladen!');
            }
        }, undefined, (err) => console.error('VRMA Animation Fehler:', err));
    },
    (progress) => console.log('Loading...', (progress.loaded / progress.total * 100).toFixed(0) + '%'),
    (error) => console.error('VRM Fehler:', error)
);

// ==========================================
// LIPSYNC + BLINKING + IDLE ANIMATION
// ==========================================
let mouthOpenValue  = 0;
let targetMouthOpen = 0;
let blinkTimer      = 0;
let isBlinking      = false;
let totalTime       = 0;

function updateExpressions(delta) {
    if (!currentVrm) return;

    totalTime += delta;

    mouthOpenValue += (targetMouthOpen - mouthOpenValue) * 0.3;
    currentVrm.expressionManager?.setValue('aa', mouthOpenValue);

    blinkTimer += delta;
    if (!isBlinking && blinkTimer > 3.0 + Math.random() * 2.0) {
        isBlinking = true;
        blinkTimer = 0;
    }
    if (isBlinking) {
        const blinkVal = Math.sin(blinkTimer * Math.PI / 0.15);
        const clampedBlink = Math.max(0, Math.min(1, blinkVal));
        currentVrm.expressionManager?.setValue('blink', clampedBlink);
        if (blinkTimer > 0.15) {
            isBlinking = false;
            currentVrm.expressionManager?.setValue('blink', 0);
        }
    }

    const bones = currentVrm.humanoid;
    if (bones) {
        const headBone = bones.getNormalizedBoneNode('head');
        if (headBone) {
            headBone.rotation.x = Math.sin(totalTime * 0.5) * 0.1;
            headBone.rotation.z = Math.sin(totalTime * 0.4) * 0.1;
            headBone.rotation.y = Math.sin(totalTime * 0.3) * 0.2;
        }

        const hipBone = bones.getNormalizedBoneNode('hips');
        if (hipBone) {
            hipBone.position.y = Math.sin(totalTime * 0.8) * 0.015;
            hipBone.rotation.z = Math.sin(totalTime * 0.6) * 0.025;
        }
    }

    currentVrm.update(delta);
}

// ==========================================
// SOCKET.IO -- empfängt Lipsync + Untertitel von Flask
// ==========================================
const wsDot  = document.getElementById('ws-dot');
const wsText = document.getElementById('ws-text');
const subtitle = document.getElementById('subtitle');
let subtitleTimer = null;

const socket = io(PYTHON_SERVER);

socket.on('connect', () => {
    wsDot.classList.add('connected');
    wsText.textContent = 'Aki connected';
    console.log('✅ Socket.IO verbunden!');
});

socket.on('disconnect', () => {
    wsDot.classList.remove('connected');
    wsText.textContent = 'Reconnecting...';
});

socket.on('mouth', (data) => {
    targetMouthOpen = Math.min(data.value * 1.5, 1.0);
});

socket.on('mouth_close', () => {
    targetMouthOpen = 0;
});

socket.on('subtitle', (data) => {
    subtitle.textContent = data.text;
    subtitle.classList.add('visible');
    clearTimeout(subtitleTimer);
    subtitleTimer = setTimeout(() => {
        subtitle.classList.remove('visible');
    }, data.duration || 4000);
});

// ==========================================
// AKI IM FENSTER VERSCHIEBBAR MACHEN (per Raycasting auf das Modell)
// ==========================================
let isDraggingCharacter = false;
let dragStart = { x: 0, y: 0 };
let modelStartPos = { x: 0, y: 0 };

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

renderer.domElement.addEventListener('pointerdown', (event) => {
    if (!currentVrm) return;
    
    // Korrekte Normalisierung der Mauskoordinaten
    pointer.x = (event.clientX / window.innerWidth) * 2 - 1;
    pointer.y = -(event.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);

    // Wir durchsuchen die gesamte Szene des Modells rekursiv nach getroffenen Meshes
    const intersects = raycaster.intersectObjects(currentVrm.scene.children, true);
    
    if (intersects.length > 0) {
        isDraggingCharacter = true;
        dragStart.x = event.clientX;
        dragStart.y = event.clientY;
        
        // Startposition der gesamten VRM-Szene sichern
        modelStartPos.x = currentVrm.scene.position.x;
        modelStartPos.y = currentVrm.scene.position.y;
        
        renderer.domElement.style.cursor = 'grabbing';
    }
});

window.addEventListener('pointermove', (event) => {
    if (!isDraggingCharacter || !currentVrm) return;
    
    const deltaX = event.clientX - dragStart.x;
    const deltaY = event.clientY - dragStart.y;
    
    // Faktor leicht erhöht für ein direkteres Verschiebegefühl auf dem Bildschirm
    const scale = 0.0035; 
    
    currentVrm.scene.position.x = modelStartPos.x + deltaX * scale;
    currentVrm.scene.position.y = modelStartPos.y - deltaY * scale;
});

window.addEventListener('pointerup', () => {
    isDraggingCharacter = false;
    renderer.domElement.style.cursor = 'default';
});
// ==========================================
// INPUT CONTROLS -> ROUTING ZU PYTHON/FLASK
// ==========================================
const modeToggle = document.getElementById('modeToggle');
const agentInput = document.getElementById('agentInput');
const modeLabel = document.getElementById('modeLabel');

modeToggle.addEventListener('change', () => {
    const mode = modeToggle.checked ? 'agent' : 'normal';
    agentInput.style.display = mode === 'agent' ? 'block' : 'none';
    modeLabel.textContent = mode === 'agent' ? 'Agent-Modus' : 'Normal-Modus';

    fetch(`${PYTHON_SERVER}/set_mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode })
    }).catch(err => console.error('Fehler beim Senden des Modus an Python:', err));
});

agentInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        const text = agentInput.value.trim();
        if (!text) return;

        fetch(`${PYTHON_SERVER}/agent_input`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ value: text })
        }).catch(err => console.error('Fehler beim Senden des Inputs an Python:', err));

        agentInput.value = '';
    }
});

// ==========================================
// RENDER LOOP
// ==========================================
const clock = new THREE.Clock();
function animate() {
    requestAnimationFrame(animate);
    const delta = clock.getDelta();
    if (mixer) mixer.update(delta);
    updateExpressions(delta);
    renderer.render(scene, camera);
}
animate();

window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});
