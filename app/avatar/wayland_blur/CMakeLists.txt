cmake_minimum_required(VERSION 3.16)
project(WaylandBlurPlugin LANGUAGES CXX C)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Qt6 REQUIRED COMPONENTS Core Gui GuiPrivate Qml Quick WaylandClient)

qt_add_qml_module(waylandblurplugin
    URI WaylandBlur
    VERSION 1.0
    PLUGIN_TARGET waylandblurplugin
    NO_PLUGIN_OPTIONAL
    OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR}/WaylandBlur
    SOURCES
        backgroundblur.h
        backgroundblur.cpp
        qwayland-ext-background-effect-v1.h
        qwayland-ext-background-effect-v1.cpp
        wayland-ext-background-effect-v1-client-protocol.h
        wayland-ext-background-effect-v1-client-protocol.c
)

target_include_directories(waylandblurplugin PRIVATE ${CMAKE_CURRENT_SOURCE_DIR})

target_link_libraries(waylandblurplugin PRIVATE
    Qt6::Core
    Qt6::Gui
    Qt6::GuiPrivate
    Qt6::Qml
    Qt6::Quick
    Qt6::WaylandClient
)
