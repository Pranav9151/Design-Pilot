import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { cn } from '@/lib/utils'
import { Loader2, RotateCcw, Box } from 'lucide-react'

// Lazy-load GLTFLoader (not in Three.js core bundle in r128)
// We use a minimal orbit-controls implementation to avoid CDN dependency
function createOrbitControls(camera: THREE.PerspectiveCamera, domElement: HTMLElement) {
  let isPointerDown = false
  let lastX = 0, lastY = 0
  let azimuth = Math.PI / 4
  let elevation = Math.PI / 6
  let radius = 250

  function updateCamera() {
    camera.position.x = radius * Math.cos(elevation) * Math.sin(azimuth)
    camera.position.y = radius * Math.sin(elevation)
    camera.position.z = radius * Math.cos(elevation) * Math.cos(azimuth)
    camera.lookAt(0, 0, 0)
  }

  updateCamera()

  const onPointerDown = (e: PointerEvent) => { isPointerDown = true; lastX = e.clientX; lastY = e.clientY }
  const onPointerUp   = () => { isPointerDown = false }
  const onPointerMove = (e: PointerEvent) => {
    if (!isPointerDown) return
    const dx = (e.clientX - lastX) * 0.005
    const dy = (e.clientY - lastY) * 0.005
    azimuth   -= dx
    elevation  = Math.max(-1.2, Math.min(1.2, elevation - dy))
    lastX = e.clientX; lastY = e.clientY
    updateCamera()
  }
  const onWheel = (e: WheelEvent) => {
    radius = Math.max(80, Math.min(800, radius + e.deltaY * 0.2))
    updateCamera()
  }

  domElement.addEventListener('pointerdown', onPointerDown)
  domElement.addEventListener('pointerup',   onPointerUp)
  domElement.addEventListener('pointermove', onPointerMove)
  domElement.addEventListener('wheel',       onWheel, { passive: true })

  return {
    reset: () => { azimuth = Math.PI / 4; elevation = Math.PI / 6; radius = 250; updateCamera() },
    dispose: () => {
      domElement.removeEventListener('pointerdown', onPointerDown)
      domElement.removeEventListener('pointerup',   onPointerUp)
      domElement.removeEventListener('pointermove', onPointerMove)
      domElement.removeEventListener('wheel',       onWheel)
    },
  }
}

interface Props {
  glbUrl: string | null
  className?: string
}

export function ModelViewer({ glbUrl, className }: Props) {
  const mountRef = useRef<HTMLDivElement>(null)
  const resetRef = useRef<(() => void) | null>(null)
  const [loading, setLoading] = useState(Boolean(glbUrl))
  const [error,   setError]   = useState<string | null>(null)

  useEffect(() => {
    const el = mountRef.current
    if (!el) return

    const w = el.clientWidth  || 600
    const h = el.clientHeight || 400

    // Scene
    const scene    = new THREE.Scene()
    scene.background = new THREE.Color(0x0F1011)

    // Grid
    const grid = new THREE.GridHelper(300, 20, 0x1E2022, 0x1E2022)
    grid.position.y = -30
    scene.add(grid)

    // Lights
    const ambient = new THREE.AmbientLight(0xffffff, 0.4)
    scene.add(ambient)
    const key = new THREE.DirectionalLight(0xffffff, 1.2)
    key.position.set(150, 200, 100)
    scene.add(key)
    const fill = new THREE.DirectionalLight(0x3B82F6, 0.3)
    fill.position.set(-100, 50, -80)
    scene.add(fill)

    // Camera
    const camera = new THREE.PerspectiveCamera(45, w / h, 0.1, 5000)
    const controls = createOrbitControls(camera, el)
    resetRef.current = controls.reset

    // Renderer
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(w, h)
    renderer.shadowMap.enabled = true
    el.appendChild(renderer.domElement)

    // Placeholder geometry
    const mat = new THREE.MeshStandardMaterial({ color: 0x3B82F6, roughness: 0.35, metalness: 0.65 })
    const placeholder = new THREE.Group()

    const base = new THREE.Mesh(new THREE.BoxGeometry(80, 8, 60), mat)
    placeholder.add(base)
    const wall = new THREE.Mesh(new THREE.BoxGeometry(80, 50, 6), mat)
    wall.position.set(0, 29, -27)
    placeholder.add(wall)

    // Animate render loop
    let animId = 0

    scene.add(placeholder)

    const render = () => {
      animId = requestAnimationFrame(render)
      renderer.render(scene, camera)
    }
    render()

    // Resize observer
    const ro = new ResizeObserver(() => {
      const nw = el.clientWidth
      const nh = el.clientHeight
      if (nw > 0 && nh > 0) {
        camera.aspect = nw / nh
        camera.updateProjectionMatrix()
        renderer.setSize(nw, nh)
      }
    })
    ro.observe(el)

    // Load GLB if provided
    let aborted = false
    if (glbUrl) {
      ;(async () => {
        try {
          const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js').catch(() => {
            // Fallback: keep placeholder, log warning
            console.warn('[ModelViewer] GLTFLoader not available — showing placeholder geometry')
            return { GLTFLoader: null }
          })

          if (!GLTFLoader || aborted) { setLoading(false); return }

          const loader = new GLTFLoader()
          const gltf = await new Promise<{ scene: THREE.Group }>((res, rej) =>
            loader.load(glbUrl, res, undefined, rej)
          )

          if (aborted) return

          scene.remove(placeholder)
          const model = gltf.scene
          // Auto-center and scale
          const box = new THREE.Box3().setFromObject(model)
          const center = box.getCenter(new THREE.Vector3())
          const size   = box.getSize(new THREE.Vector3()).length()
          model.position.sub(center)
          const scale = 160 / size
          model.scale.setScalar(scale)
          scene.add(model)
        } catch (err) {
          if (!aborted) {
            setError('Failed to load 3D model — showing placeholder')
            console.warn('[ModelViewer]', err)
          }
        } finally {
          if (!aborted) setLoading(false)
        }
      })()
    }

    return () => {
      aborted = true
      cancelAnimationFrame(animId)
      controls.dispose()
      ro.disconnect()
      renderer.dispose()
      if (el.contains(renderer.domElement)) el.removeChild(renderer.domElement)
    }
  }, [glbUrl])

  return (
    <div className={cn('relative bg-bg-1 overflow-hidden', className)}>
      {/* Three.js mount */}
      <div ref={mountRef} className="three-canvas w-full h-full" />

      {/* Loading overlay */}
      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-bg-1/60 backdrop-blur-sm">
          <div className="flex items-center gap-2 font-mono text-xs text-text-muted">
            <Loader2 className="h-4 w-4 animate-spin text-blue" />
            Loading 3D model…
          </div>
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="absolute bottom-0 left-0 right-0 bg-amber/10 border-t border-amber/30 px-4 py-2">
          <p className="font-mono text-2xs text-amber">{error}</p>
        </div>
      )}

      {/* Controls overlay */}
      <div className="absolute top-3 left-3 flex flex-col gap-1.5">
        <div className="flex items-center gap-1.5 bg-bg-3/80 border border-border px-2 py-1 backdrop-blur-sm">
          <Box className="h-3 w-3 text-text-faint" />
          <span className="font-mono text-2xs text-text-faint">3D PREVIEW</span>
        </div>
        {!glbUrl && (
          <div className="bg-bg-3/80 border border-border px-2 py-1 backdrop-blur-sm">
            <span className="font-mono text-2xs text-text-faint">PLACEHOLDER</span>
          </div>
        )}
      </div>

      {/* Reset camera button */}
      <button
        onClick={() => resetRef.current?.()}
        className="absolute top-3 right-3 p-1.5 bg-bg-3/80 border border-border backdrop-blur-sm text-text-faint hover:text-text transition-colors"
        title="Reset camera"
      >
        <RotateCcw className="h-3.5 w-3.5" />
      </button>

      {/* Interaction hint */}
      <div className="absolute bottom-3 right-3">
        <span className="font-mono text-2xs text-text-faint/60">
          drag · scroll
        </span>
      </div>
    </div>
  )
}
