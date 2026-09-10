#pragma once

#include <QQuickItem>
#include <QtWaylandClient/QWaylandClientExtension>

#include "qwayland-ext-background-effect-v1.h"

// Binds the ext_background_effect_manager_v1 global (compositor-supported
// real blur, distinct from the older org_kde_kwin_blur_manager protocol
// that Plasma 6.7 removed). QWaylandClientExtensionTemplate handles the
// wl_registry bind/rebind lifecycle for us.
class BackgroundEffectManager : public QWaylandClientExtensionTemplate<BackgroundEffectManager>,
                                 public QtWayland::ext_background_effect_manager_v1
{
public:
    BackgroundEffectManager();
};

// QML item: place it as a child of whatever Item should read as
// "glass" (e.g. the panel background). It tracks its own mapped
// geometry in the window and keeps the compositor's blur region in
// sync. Falls back to a silent no-op if the compositor doesn't
// advertise the protocol (e.g. non-KWin, or an older KWin) - callers
// should keep their simulated-glass visuals as a fallback look.
class BackgroundBlur : public QQuickItem
{
    Q_OBJECT
    QML_ELEMENT
    Q_PROPERTY(bool active READ active WRITE setActive NOTIFY activeChanged)
    Q_PROPERTY(bool supported READ supported NOTIFY supportedChanged)

public:
    explicit BackgroundBlur(QQuickItem *parent = nullptr);
    ~BackgroundBlur() override;

    bool active() const { return m_active; }
    void setActive(bool active);

    bool supported() const { return m_surfaceEffect != nullptr; }

signals:
    void activeChanged();
    void supportedChanged();

protected:
    void itemChange(ItemChange change, const ItemChangeData &data) override;
    void geometryChange(const QRectF &newGeometry, const QRectF &oldGeometry) override;

private:
    void tryAttach();
    void updateRegion();
    void clearRegion();

    bool m_active = true;
    BackgroundEffectManager *m_manager = nullptr;
    QtWayland::ext_background_effect_surface_v1 *m_surfaceEffect = nullptr;
};
