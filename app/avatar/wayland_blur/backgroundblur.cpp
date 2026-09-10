#include "backgroundblur.h"

#include <QGuiApplication>
#include <QLoggingCategory>
#include <QQuickWindow>
#include <QtGui/qguiapplication_platform.h>
#include <QtGui/qpa/qplatformnativeinterface.h>

#include <wayland-client-protocol.h>

Q_LOGGING_CATEGORY(lcBlur, "screencontext.blur")

BackgroundEffectManager::BackgroundEffectManager()
    : QWaylandClientExtensionTemplate<BackgroundEffectManager>(1)
{
}

BackgroundBlur::BackgroundBlur(QQuickItem *parent)
    : QQuickItem(parent)
{
    setFlag(ItemHasContents, false);
    m_manager = new BackgroundEffectManager();
    connect(m_manager, &QWaylandClientExtension::activeChanged, this, &BackgroundBlur::tryAttach);
}

BackgroundBlur::~BackgroundBlur()
{
    delete m_surfaceEffect;
    delete m_manager;
}

void BackgroundBlur::setActive(bool active)
{
    if (m_active == active)
        return;
    m_active = active;
    emit activeChanged();
    if (m_active)
        updateRegion();
    else
        clearRegion();
}

void BackgroundBlur::itemChange(ItemChange change, const ItemChangeData &data)
{
    QQuickItem::itemChange(change, data);
    if (change == ItemSceneChange && data.window) {
        connect(data.window, &QQuickWindow::sceneGraphInitialized,
                this, &BackgroundBlur::tryAttach, Qt::UniqueConnection);
        tryAttach();
    }
}

void BackgroundBlur::geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry)
{
    QQuickItem::geometryChange(newGeometry, oldGeometry);
    updateRegion();
}

void BackgroundBlur::tryAttach()
{
    if (m_surfaceEffect || !window() || !m_manager->isActive())
        return;

    QPlatformNativeInterface *native = qGuiApp->platformNativeInterface();
    if (!native)
        return;

    auto *surface = static_cast<struct wl_surface *>(
        native->nativeResourceForWindow(QByteArrayLiteral("surface"), window()));
    if (!surface) {
        // Native platform window not created yet - retry after the next
        // frame, once QQuickWindow has actually realized it.
        connect(window(), &QQuickWindow::frameSwapped,
                this, &BackgroundBlur::tryAttach, Qt::UniqueConnection);
        return;
    }
    disconnect(window(), &QQuickWindow::frameSwapped, this, &BackgroundBlur::tryAttach);

    auto *raw = m_manager->get_background_effect(surface);
    if (!raw) {
        qCWarning(lcBlur) << "compositor refused ext_background_effect_surface_v1";
        return;
    }
    m_surfaceEffect = new QtWayland::ext_background_effect_surface_v1(raw);
    emit supportedChanged();
    qCInfo(lcBlur) << "real compositor blur attached";
    updateRegion();
}

void BackgroundBlur::updateRegion()
{
    if (!m_surfaceEffect || !window() || !m_active)
        return;

    auto *waylandApp = qGuiApp->nativeInterface<QNativeInterface::QWaylandApplication>();
    struct wl_compositor *compositor = waylandApp ? waylandApp->compositor() : nullptr;
    if (!compositor) {
        qCWarning(lcBlur) << "no wl_compositor handle available";
        return;
    }

    const QRectF r = mapRectToScene(boundingRect());
    if (r.isEmpty())
        return;

    struct wl_region *region = wl_compositor_create_region(compositor);
    wl_region_add(region, qRound(r.x()), qRound(r.y()), qRound(r.width()), qRound(r.height()));
    m_surfaceEffect->set_blur_region(region);
    wl_region_destroy(region); // copy semantics per protocol - safe to destroy immediately
    window()->update();
}

void BackgroundBlur::clearRegion()
{
    if (!m_surfaceEffect)
        return;
    m_surfaceEffect->set_blur_region(nullptr);
    if (window())
        window()->update();
}
