cmake_minimum_required(VERSION 3.21)
project(GoiOverlay LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
# Qt 6 headers need C++17 in the Objective-C++ file (macOS) too; older Xcode defaults to less.
set(CMAKE_OBJCXX_STANDARD 17)
set(CMAKE_OBJCXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

if(APPLE AND NOT CMAKE_PREFIX_PATH)
  execute_process(COMMAND brew --prefix qt OUTPUT_VARIABLE BREW_QT
                  OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
  if(BREW_QT)
    list(APPEND CMAKE_PREFIX_PATH "${BREW_QT}")
  endif()
endif()

find_package(Qt6 6.5 REQUIRED COMPONENTS Widgets Network)

set(SRC src/main.cpp src/Overlay.cpp)
if(WIN32)
  list(APPEND SRC src/platform_win.cpp)
elseif(APPLE)
  enable_language(OBJCXX)
  list(APPEND SRC src/platform_mac.mm)
else()
  list(APPEND SRC src/platform_linux.cpp)
endif()

add_executable(goi-overlay ${SRC})
target_link_libraries(goi-overlay PRIVATE Qt6::Widgets Qt6::Network)

if(APPLE)
  target_link_libraries(goi-overlay PRIVATE "-framework AppKit")
endif()

if(MSVC)
  target_compile_options(goi-overlay PRIVATE /utf-8)  # sources are UTF-8; MSVC assumes the ANSI code page
endif()

if(WIN32)
  # windows.h must not define min/max macros (they break std::min/std::max) or pull in everything
  target_compile_definitions(goi-overlay PRIVATE NOMINMAX WIN32_LEAN_AND_MEAN)

  # By default the program is a console app, so log lines (e.g. "could not bind port") are visible.
  # Turn this off to run without a console window.
  option(GOI_OVERLAY_CONSOLE "Windows: show a console window (log output)" ON)
  if(NOT GOI_OVERLAY_CONSOLE)
    set_target_properties(goi-overlay PROPERTIES WIN32_EXECUTABLE ON)
  endif()

  # Copy the Qt DLLs and plugins next to the exe, so it also starts outside the Qt installation's PATH.
  option(GOI_DEPLOY_QT "Windows: run windeployqt after building" ON)
  if(GOI_DEPLOY_QT)
    find_program(GOI_WINDEPLOYQT windeployqt
                 HINTS "${QT6_INSTALL_PREFIX}/${QT6_INSTALL_BINS}" "${Qt6_DIR}/../../../bin")
    if(GOI_WINDEPLOYQT)
      add_custom_command(TARGET goi-overlay POST_BUILD
        COMMAND "${GOI_WINDEPLOYQT}" --no-translations "$<TARGET_FILE:goi-overlay>"
        COMMENT "Running windeployqt"
        VERBATIM)
    else()
      message(WARNING "windeployqt not found: goi-overlay.exe will only start if the Qt bin folder is on PATH")
    endif()
  endif()
endif()

if(UNIX AND NOT APPLE)
  # KDE's Qt6 binding for wlr-layer-shell (needed for overlays on Wayland)
  find_package(LayerShellQt QUIET)
  if(LayerShellQt_FOUND)
    target_link_libraries(goi-overlay PRIVATE LayerShellQt::Interface)
    target_compile_definitions(goi-overlay PRIVATE HAVE_LAYERSHELL)
  else()
    message(WARNING "LayerShellQt not found: overlay will NOT sit above fullscreen windows on Wayland")
  endif()
endif()