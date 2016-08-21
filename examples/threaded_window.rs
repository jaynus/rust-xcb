extern crate xcb;

use std::iter::{Iterator};
use std::{thread, time};
use std::sync::Arc;

fn main() {
    let (conn, screen_num) = xcb::Connection::connect(None).unwrap();
    let (screen_root, screen_wp, screen_rv) = {
        let setup = conn.get_setup();
        let screen = setup.roots().nth(screen_num as usize).unwrap();
        (screen.root(), screen.white_pixel(), screen.root_visual())
    };

    let window = conn.generate_id();

    let values = [
        (xcb::CW_BACK_PIXEL, screen_wp),
        (xcb::CW_EVENT_MASK, xcb::EVENT_MASK_EXPOSURE | xcb::EVENT_MASK_KEY_PRESS |
            xcb::EVENT_MASK_STRUCTURE_NOTIFY | xcb::EVENT_MASK_PROPERTY_CHANGE),
    ];

    xcb::create_window(&conn,
        xcb::COPY_FROM_PARENT as u8,
        window,
        screen_root,
        0, 0,
        150, 150,
        10,
        xcb::WINDOW_CLASS_INPUT_OUTPUT as u16,
        screen_rv,
        &values);

    xcb::map_window(&conn, window);

    let conn = Arc::new(conn);

    {
        let conn = conn.clone();
        thread::spawn(move || {
            let mut smiley = false;
            loop {
                let title = if smiley {
                    "Basic Threaded Window ;-)"
                }
                else {
                    "Basic Threaded Window"
                };

                let c = xcb::change_property_checked(&conn, xcb::PROP_MODE_REPLACE as u8, window,
                        xcb::ATOM_WM_NAME, xcb::ATOM_STRING, 8, title.as_bytes());

                if conn.has_error().is_err() || c.request_check().is_err() {
                    break;
                }

                smiley = !smiley;
                thread::sleep(time::Duration::from_millis(500));
            }
        });
    }

    conn.flush();

    loop {
        let event = conn.wait_for_event();
        match event {
            None => { break; }
            Some(event) => {
                let r = event.response_type();
                if r == xcb::KEY_PRESS as u8 {
                    let key_press : &xcb::KeyPressEvent = xcb::cast_event(&event);

                    println!("Key '{}' pressed", key_press.detail());

                    if key_press.detail() == 0x18 { // Q (on qwerty)
                        break;
                    }
                }
            }
        }
    }
}