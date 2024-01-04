/// Example of a Rust Library

/// Generates a String containing "Hello"
///
/// # Examples
///
/// ```
/// use hello_lib::hello;
///
/// let hello_string = hello();
/// ```
pub fn hello() -> String {
    message_string("Hello")
}

/// Generates a String from a String reference
///
/// This is a private function used by the public API function.
fn message_string(message: &str) -> String {
    String::from(message)
}

#[cfg(test)]
mod test {
    use super::message_string;

    #[test]
    /// Test the private message_string() function
    fn test_message_string() {
        assert_eq!("Testing123", message_string("Testing123"));
    }
}
